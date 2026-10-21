"""Direct-mode tests for Period Rate Escrow.

The groups map onto the Steward's correction request and the lessons behind it:

  A  units        -- one asset (GEN wei) and one rate scale (1e8), enforced
  B  adapters     -- JSON nested by date, flat JSON, and CSV quoted on EUR
  C  coverage     -- a provider counts only when it covers the exact window
  D  cross-check  -- one source never decides; a wide spread is refused
  E  agreement    -- validators bind the payout, the spread and every average
  F  funds        -- payout = notional * average // strike, and conservation
  G  exits        -- cancel and expire are the recovery paths
  H  preconditions-- every refusal leaves the state untouched
"""

import json

import pytest

from conftest import (
    AVG,
    BAD_ADDR,
    BUYER,
    DAYS,
    ESCROW,
    NOTIONAL,
    NOW,
    PAYOUT,
    RATE_SCALE,
    RATES,
    SCALED,
    SELLER,
    STRIKE,
    STRANGER,
    balance,
    contract_balance,
    curapi,
    deal,
    ecb,
    frankfurter,
    full_snapshot,
    happy,
    iso,
    open_deal,
    outcome,
    rates,
    remock,
    row,
    settle,
)


# ==========================================================================
# A. one unit model
# ==========================================================================


def test_open_settlement_escrows_the_funds(escrow):
    vm, c = escrow
    before = contract_balance(vm)
    did = open_deal(vm, c)
    assert contract_balance(vm) == before + ESCROW
    d = deal(vm, c, did)
    assert d["status"] == "OPEN"
    assert d["buyer"] == BUYER
    assert d["seller"] == SELLER
    assert d["days"] == DAYS
    assert d["base"] == "USD" and d["quote"] == "CNY"


def test_the_deal_records_the_unit_model(escrow):
    vm, c = escrow
    d = deal(vm, c, open_deal(vm, c))
    assert d["units"] == {"asset": "GEN", "amount_decimals": 18,
                          "rate_decimals": 8, "rate": "quote per base"}
    # every amount is an integer of wei, every rate an integer of 1e-8
    assert d["notional"] == NOTIONAL
    assert d["escrow"] == ESCROW
    assert d["strike"] == STRIKE
    assert RATE_SCALE == 10 ** d["units"]["rate_decimals"]


def test_the_escrow_is_the_native_value_in_wei(escrow):
    vm, c = escrow
    before = balance(vm, BUYER)
    open_deal(vm, c)
    assert balance(vm, BUYER) == before - ESCROW
    assert contract_balance(vm) == ESCROW


def test_rejects_a_bad_seller(escrow):
    vm, c = escrow
    for bad in (BAD_ADDR, BUYER, "0x" + "zz" * 20, ""):
        with pytest.raises(Exception):
            open_deal(vm, c, seller=bad)
        assert c.get_deal_count() == 0


def test_rejects_a_zero_escrow(escrow):
    vm, c = escrow
    with pytest.raises(Exception):
        open_deal(vm, c, escrow_amt=0)
    assert balance(vm, BUYER) > 0


def test_rejects_a_bad_currency_pair(escrow):
    vm, c = escrow
    for b, q in (("USD", "USD"), ("US", "CNY"), ("USDD", "CNY"), ("U1D", "CNY")):
        with pytest.raises(Exception):
            open_deal(vm, c, base=b, quote=q)


def test_rejects_a_bad_window(escrow):
    vm, c = escrow
    for days in ([], DAYS + ["2025-12-28", "2025-12-27", "2025-12-26"],
                 ["2025/12/29"], ["2025-12-29", "2025-12-29"]):
        with pytest.raises(Exception):
            open_deal(vm, c, days=days)


def test_rejects_a_window_that_has_not_ended(escrow):
    vm, c = escrow
    with pytest.raises(Exception):
        open_deal(vm, c, days=["2026-01-05"])


def test_the_window_is_stored_in_order(escrow):
    vm, c = escrow
    d = deal(vm, c, open_deal(vm, c, days=[DAYS[2], DAYS[0], DAYS[1]]))
    assert d["days"] == DAYS


def test_rejects_a_bad_notional_or_tolerance(escrow):
    vm, c = escrow
    for n in (0, -1):
        with pytest.raises(Exception):
            open_deal(vm, c, notional=n)
    for t in (-1, 5001):
        with pytest.raises(Exception):
            open_deal(vm, c, tolerance=t)


@pytest.mark.parametrize("strike", [0, -1, 1, 672, 10 ** 13])
def test_rejects_a_strike_that_is_not_a_1e8_rate(escrow, strike):
    """6.72 instead of 672000000 is the classic unit bug; it must not open."""
    vm, c = escrow
    with pytest.raises(Exception):
        open_deal(vm, c, strike=strike)
    assert c.get_deal_count() == 0


def test_accepts_a_strike_at_the_edges(escrow):
    vm, c = escrow
    for strike in (10 ** 4, 10 ** 12):
        did = open_deal(vm, c, strike=strike)
        assert deal(vm, c, did)["strike"] == strike


def test_rejects_a_bad_provider_list(escrow):
    vm, c = escrow
    for ps in (["frankfurter"], ["frankfurter", "frankfurter"],
               ["frankfurter", "made_up"]):
        with pytest.raises(Exception):
            open_deal(vm, c, providers=ps)


def test_rejects_bad_timings(escrow):
    vm, c = escrow
    for after, deadline in ((0, 100), (100, 100), (100, 50)):
        with pytest.raises(Exception):
            open_deal(vm, c, settle_after=after, deadline=deadline)


def test_a_refused_open_moves_no_funds(escrow):
    vm, c = escrow
    before = balance(vm, BUYER)
    held = contract_balance(vm)
    with pytest.raises(Exception):
        open_deal(vm, c, strike=7)
    assert balance(vm, BUYER) == before
    assert contract_balance(vm) == held


# ==========================================================================
# B. provider adapters
# ==========================================================================


def test_all_three_real_shapes_land_on_one_scale(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c)
    settle(vm, c, did)
    rows = rates(vm, c, did)
    assert [r["provider"] for r in rows] == ["frankfurter", "ecb", "currencyapi"]
    assert [r["average"] for r in rows] == [AVG, AVG, AVG]
    assert all(r["covered"] for r in rows)


def test_ecb_is_a_cross_rate_not_a_direct_one(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c)
    settle(vm, c, did)
    ecb_row = [r for r in rates(vm, c, did) if r["provider"] == "ecb"][0]
    # CNY/EUR divided by USD/EUR, not either leg on its own
    assert ecb_row["average"] == AVG


def test_a_rate_string_and_a_rate_number_agree(escrow):
    vm, c = escrow
    remock(vm, fr=frankfurter(rates=["6.72", "6.7215", "6.7191"]),
           ca={i: curapi(i) for i in range(len(DAYS))},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    assert settle(vm, c, did) == "EXECUTED"
    assert deal(vm, c, did)["average"] == AVG


def test_rejects_a_frankfurter_entry_for_another_base(escrow):
    vm, c = escrow
    remock(vm, fr=frankfurter(base="EUR"),
           ca={i: curapi(i) for i in range(len(DAYS))},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    settle(vm, c, did)
    fr = [r for r in rates(vm, c, did) if r["provider"] == "frankfurter"][0]
    assert fr["days"] == 0 and fr["covered"] is False


def test_rejects_a_currencyapi_entry_for_another_currency(escrow):
    vm, c = escrow
    bad = {i: json.dumps({"date": DAYS[i], "usd": {"eur": RATES[i]}}) for i in range(len(DAYS))}
    remock(vm, fr=frankfurter(), ca=bad, ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    settle(vm, c, did)
    ca = [r for r in rates(vm, c, did) if r["provider"] == "currencyapi"][0]
    assert ca["days"] == 0 and ca["covered"] is False


def test_rejects_csv_without_the_expected_columns(escrow):
    vm, c = escrow
    remock(vm, fr=frankfurter(), ca={i: curapi(i) for i in range(len(DAYS))},
           ec="period,value\n" + "\n".join(DAYS), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    settle(vm, c, did)
    ecb_row = [r for r in rates(vm, c, did) if r["provider"] == "ecb"][0]
    assert ecb_row["covered"] is False


def test_rejects_a_payload_that_is_not_json(escrow):
    vm, c = escrow
    remock(vm, fr="<html>nope</html>", ca={i: curapi(i) for i in range(len(DAYS))},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    settle(vm, c, did)
    fr = [r for r in rates(vm, c, did) if r["provider"] == "frankfurter"][0]
    assert fr["days"] == 0


def test_rejects_a_payload_missing_rates(escrow):
    vm, c = escrow
    remock(vm, fr=json.dumps({"base": "USD"}),
           ca={i: curapi(i) for i in range(len(DAYS))},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    settle(vm, c, did)
    fr = [r for r in rates(vm, c, did) if r["provider"] == "frankfurter"][0]
    assert fr["days"] == 0


# ==========================================================================
# C. exact coverage
# ==========================================================================


def test_a_provider_covering_part_of_the_window_is_not_valid(escrow):
    vm, c = escrow
    # Frankfurter answers only two of the three requested days.
    remock(vm, fr=frankfurter(rates=[6.72, 6.7215], days=[DAYS[0], DAYS[1]]),
           ca={i: curapi(i) for i in range(len(DAYS))},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    assert settle(vm, c, did) == "EXECUTED"
    d = deal(vm, c, did)
    assert d["valid_count"] == 2
    fr = [r for r in rates(vm, c, did) if r["provider"] == "frankfurter"][0]
    assert fr["days"] == 2 and fr["covered"] is False


def test_a_short_provider_does_not_shift_the_average(escrow):
    vm, c = escrow
    # The two-day answer is wildly off; it must not enter the average at all.
    remock(vm, fr=frankfurter(rates=[9.9, 9.9], days=[DAYS[0], DAYS[1]]),
           ca={i: curapi(i) for i in range(len(DAYS))},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    assert settle(vm, c, did) == "EXECUTED"
    d = deal(vm, c, did)
    assert d["average"] == AVG
    assert d["payout"] == PAYOUT


def test_two_short_providers_leave_nothing_to_settle(escrow):
    vm, c = escrow
    remock(vm, fr=frankfurter(rates=[6.72, 6.7215], days=[DAYS[0], DAYS[1]]),
           ca={i: curapi(i) for i in (0, 1)},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    assert settle(vm, c, did) == "REFUNDED"
    d = deal(vm, c, did)
    assert d["valid_count"] == 1
    assert d["reason"] == "the period average could not be verified"
    assert d["buyer_refund"] == ESCROW


def test_a_missing_day_on_one_provider_is_reported_as_coverage(escrow):
    vm, c = escrow
    remock(vm, fr=frankfurter(),
           ca={i: curapi(i) for i in (0, 2)},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))
    did = open_deal(vm, c)
    settle(vm, c, did)
    ca = [r for r in rates(vm, c, did) if r["provider"] == "currencyapi"][0]
    assert ca["days"] == 2 and ca["covered"] is False
    assert deal(vm, c, did)["valid_count"] == 2


# ==========================================================================
# D. cross-validation
# ==========================================================================


def test_the_middle_average_is_used(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c)
    settle(vm, c, did)
    assert deal(vm, c, did)["average"] == AVG


def test_two_providers_are_enough(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c, providers=("frankfurter", "ecb"))
    assert settle(vm, c, did) == "EXECUTED"
    assert deal(vm, c, did)["valid_count"] == 2


def test_one_provider_alone_never_settles(escrow):
    vm, c = escrow
    remock(vm, fr=frankfurter())
    did = open_deal(vm, c, providers=("frankfurter", "ecb"))
    assert settle(vm, c, did) == "REFUNDED"
    assert deal(vm, c, did)["valid_count"] == 1


def test_providers_that_disagree_beyond_tolerance_are_refused(escrow):
    vm, c = escrow
    happy(vm, ca={i: curapi(i, rate=9.0) for i in range(len(DAYS))})
    did = open_deal(vm, c, tolerance=100)
    assert settle(vm, c, did) == "REFUNDED"
    d = deal(vm, c, did)
    assert d["reason"] == "providers did not agree"
    assert d["spread_bps"] > 100


def test_a_wider_tolerance_accepts_the_same_data(escrow):
    vm, c = escrow
    happy(vm, ca={i: curapi(i, rate=9.0) for i in range(len(DAYS))})
    did = open_deal(vm, c, tolerance=5000)
    assert settle(vm, c, did) == "EXECUTED"


def test_every_decision_is_code_over_fetched_data(escrow):
    vm, c = escrow
    # No model mock exists in this fixture: nothing here asks a model anything.
    happy(vm)
    did = open_deal(vm, c)
    assert settle(vm, c, did) == "EXECUTED"


# ==========================================================================
# E. validator agreement binds the payout
# ==========================================================================


def rows_ok(average=AVG):
    return [row("frankfurter", 3, True, average),
            row("ecb", 3, True, average),
            row("currencyapi", 3, True, average)]


def test_identical_runs_agree(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), spread=0, average=AVG, payout=PAYOUT)
    assert c._agree(a, a, 250, 3) is True


def test_a_different_payout_is_refused(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    b = outcome(rows_ok(), average=AVG, payout=PAYOUT + PAYOUT // 20)
    assert c._agree(a, b, 250, 3) is False


def test_a_payout_inside_the_tolerance_agrees(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    b = outcome(rows_ok(), average=AVG, payout=PAYOUT + PAYOUT // 1000)
    assert c._agree(a, b, 250, 3) is True


def test_a_different_spread_is_refused(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), spread=10, average=AVG, payout=PAYOUT)
    b = outcome(rows_ok(), spread=900, average=AVG, payout=PAYOUT)
    assert c._agree(a, b, 250, 3) is False


def test_a_provider_average_beyond_tolerance_is_refused(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    bad = rows_ok()
    bad[1] = row("ecb", 3, True, AVG * 105 // 100)
    b = outcome(bad, average=AVG, payout=PAYOUT)
    assert c._agree(a, b, 250, 3) is False


def test_a_provider_average_inside_tolerance_agrees(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    ok = rows_ok()
    ok[1] = row("ecb", 3, True, AVG * 10001 // 10000)
    b = outcome(ok, average=AVG, payout=PAYOUT)
    assert c._agree(a, b, 250, 3) is True


def test_a_coverage_change_is_refused(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    bad = rows_ok()
    bad[0] = row("frankfurter", 2, False, AVG)
    b = outcome(bad, average=AVG, payout=PAYOUT)
    assert c._agree(a, b, 250, 3) is False


def test_a_day_count_change_is_refused(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    bad = rows_ok()
    bad[0] = row("frankfurter", 2, True, AVG)
    b = outcome(bad, average=AVG, payout=PAYOUT)
    assert c._agree(a, b, 250, 3) is False


def test_a_verdict_change_is_refused(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    b = outcome(rows_ok(), verdict="DISAGREED", average=0, payout=0)
    assert c._agree(a, b, 250, 3) is False


def test_a_valid_count_change_is_refused(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    b = outcome(rows_ok(), valid=2, average=AVG, payout=PAYOUT)
    assert c._agree(a, b, 250, 3) is False


def test_a_different_provider_set_is_refused(escrow):
    vm, c = escrow
    a = outcome(rows_ok(), average=AVG, payout=PAYOUT)
    b = outcome(list(reversed(rows_ok())), average=AVG, payout=PAYOUT)
    assert c._agree(a, b, 250, 3) is False


def test_the_agreement_band_is_deal_appropriate(escrow):
    vm, c = escrow
    assert c._agree_tol(0) == 10        # floored for feed jitter
    assert c._agree_tol(10) == 10
    assert c._agree_tol(37) == 37       # follows the deal
    assert c._agree_tol(500) == 250     # capped
    assert c._agree_tol(5000) == 250


def test_a_refunded_run_needs_no_payout(escrow):
    vm, c = escrow
    a = outcome([row("frankfurter", 3, False, 0), row("ecb", 3, False, 0),
                 row("currencyapi", 3, False, 0)],
                verdict="UNVERIFIED", valid=0, average=0, payout=0)
    assert c._agree(a, a, 250, 3) is True


# ==========================================================================
# F. funds
# ==========================================================================


def test_settles_at_the_period_average_and_returns_the_change(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c)
    seller = balance(vm, SELLER)
    buyer = balance(vm, BUYER)
    assert settle(vm, c, did) == "EXECUTED"
    d = deal(vm, c, did)
    assert d["status"] == "EXECUTED"
    assert d["reason"] == "settled at the period average"
    assert d["average"] == AVG
    assert d["payout"] == PAYOUT
    assert d["seller_paid"] == PAYOUT
    assert d["buyer_refund"] == ESCROW - PAYOUT
    assert balance(vm, SELLER) == seller + PAYOUT
    assert balance(vm, BUYER) == buyer + ESCROW - PAYOUT
    assert contract_balance(vm) == 0


def test_the_payout_is_the_ratio_of_the_two_rates(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c, strike=660000000)
    settle(vm, c, did)
    d = deal(vm, c, did)
    assert d["payout"] == (NOTIONAL * AVG) // 660000000
    assert d["payout"] > NOTIONAL          # average above the strike


def test_a_strike_equal_to_the_average_pays_exactly_the_notional(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c, strike=AVG)
    settle(vm, c, did)
    d = deal(vm, c, did)
    assert d["payout"] == NOTIONAL
    assert d["buyer_refund"] == ESCROW - NOTIONAL


def test_a_payout_of_zero_refunds(escrow):
    vm, c = escrow
    happy(vm)
    # 1000 wei of principal at a 1e12 strike rounds the payout to nothing.
    did = open_deal(vm, c, strike=10 ** 12, notional=1000)
    assert settle(vm, c, did) == "REFUNDED"
    assert deal(vm, c, did)["reason"] == "the payout came out at zero"


def test_underfunded_escrow_refunds_the_buyer(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c, strike=660000000, escrow_amt=NOTIONAL)
    buyer = balance(vm, BUYER)
    assert settle(vm, c, did) == "REFUNDED"
    d = deal(vm, c, did)
    assert d["reason"] == "the settled payout exceeds the escrow"
    assert d["buyer_refund"] == NOTIONAL
    assert balance(vm, BUYER) == buyer + NOTIONAL
    assert contract_balance(vm) == 0


def test_every_unit_reaches_exactly_one_party(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c)
    settle(vm, c, did)
    d = deal(vm, c, did)
    assert d["seller_paid"] + d["buyer_refund"] == d["escrow"]
    assert contract_balance(vm) == 0


# ==========================================================================
# G. exits
# ==========================================================================


def test_cancel_is_buyer_only_and_only_early(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    vm.sender = STRANGER
    with pytest.raises(Exception):
        c.cancel(did)
    vm.warp(iso(200))
    vm.sender = BUYER
    with pytest.raises(Exception):
        c.cancel(did)


def test_cancel_refunds_the_escrow_whole(escrow):
    vm, c = escrow
    before = balance(vm, BUYER)
    did = open_deal(vm, c)
    vm.sender = BUYER
    c.cancel(did)
    assert deal(vm, c, did)["status"] == "CANCELLED"
    assert balance(vm, BUYER) == before
    assert contract_balance(vm) == 0


def test_expire_only_after_the_deadline(escrow):
    vm, c = escrow
    did = open_deal(vm, c, settle_after=100, deadline=200)
    vm.warp(iso(150))
    vm.sender = STRANGER
    with pytest.raises(Exception):
        c.expire(did)                     # the deadline has not passed
    vm.warp(iso(300))
    assert c.expire(did) == "REFUNDED"
    assert deal(vm, c, did)["reason"] == "no settlement before the deadline"
    assert contract_balance(vm) == 0


# ==========================================================================
# H. preconditions and views
# ==========================================================================


def test_settle_only_inside_the_window(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c, settle_after=100, deadline=200)
    with pytest.raises(Exception):
        settle(vm, c, did, at=50)
    with pytest.raises(Exception):
        settle(vm, c, did, at=201)
    assert deal(vm, c, did)["status"] == "OPEN"


def test_a_settled_deal_cannot_move_again(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c)
    settle(vm, c, did)
    for call in (lambda: settle(vm, c, did), lambda: c.cancel(did), lambda: c.expire(did)):
        with pytest.raises(Exception):
            call()


def test_every_refusal_leaves_the_state_byte_identical(escrow):
    vm, c = escrow
    happy(vm)
    did = open_deal(vm, c)
    before = full_snapshot(vm, c, did)
    with pytest.raises(Exception):
        settle(vm, c, did, at=50)
    with pytest.raises(Exception):
        c.cancel(did)
    with pytest.raises(Exception):
        c.expire(did)
    assert full_snapshot(vm, c, did) == before


def test_views_return_strings_never_structures(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    s, r = full_snapshot(vm, c, did)
    assert isinstance(s, str) and isinstance(r, str)
    assert json.loads(s)["deal_id"] == did
    assert json.loads(r) == []


def test_get_deal_count_tracks_opens(escrow):
    vm, c = escrow
    assert c.get_deal_count() == 0
    open_deal(vm, c)
    open_deal(vm, c)
    assert c.get_deal_count() == 2


def test_unknown_deal_raises(escrow):
    vm, c = escrow
    with pytest.raises(Exception):
        c.get_settlement("d99")
    with pytest.raises(Exception):
        c.get_rates("d99")
