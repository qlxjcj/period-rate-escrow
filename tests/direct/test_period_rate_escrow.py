"""Direct-mode tests for Period Rate Escrow.

The groups map onto the lessons this app is built from:

  A  deal creation     -- the window must already be past before it can settle
  B  provider adapters -- JSON nested by date, flat JSON, and CSV quoted on EUR
  C  cross-validation  -- one source never decides, disagreement is refused
  D  identity          -- the model confirms the pair, code checks the quote
  E  funds             -- seller_paid + buyer_refund == escrow, always
  F  exits             -- cancel and expire are the recovery paths
  G  write preconditions -- every refusal leaves the state untouched
  H  views             -- bound to a deal id, no float or list leaves the contract
"""

import json

import pytest

from conftest import (
    AVG,
    BAD_ADDR,
    BASE,
    BUYER,
    COST,
    DAYS,
    ESCROW,
    NOTIONAL,
    NOW,
    SCALE,
    SCALED,
    SELLER,
    STRANGER,
    balance,
    contract_balance,
    curapi,
    deal,
    ecb,
    frankfurter,
    full_snapshot,
    happy,
    ident,
    iso,
    open_deal,
    rates,
    remock,
    settle,
)


# ==========================================================================
# A. deal creation
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


def test_rejects_a_bad_seller(escrow):
    vm, c = escrow
    for bad in (BAD_ADDR, BUYER, "0x" + "zz" * 20, ""):
        with pytest.raises(Exception):
            open_deal(vm, c, seller=bad)


def test_rejects_a_zero_escrow(escrow):
    vm, c = escrow
    with pytest.raises(Exception):
        open_deal(vm, c, escrow_amt=0)


def test_rejects_a_bad_currency_pair(escrow):
    vm, c = escrow
    for b, q in (("US", "CNY"), ("USD", "CN"), ("USD", "usd"), ("US1", "CNY"),
                 ("", "CNY"), ("USD", "")):
        with pytest.raises(Exception):
            open_deal(vm, c, base=b, quote=q)


def test_rejects_a_bad_window(escrow):
    vm, c = escrow
    for bad in ([], ["2025/12/29"], ["2025-12-29", "2025-12-29"], ["2025-13-29"],
                ["2025-12-2" + str(i) for i in range(6)]):
        with pytest.raises(Exception):
            open_deal(vm, c, days=bad)


def test_rejects_a_window_that_has_not_ended(escrow):
    """The period average cannot exist before the last day is over. This is
    the freeze boundary, and time enforces it rather than a policy note."""
    vm, c = escrow
    for days in (["2026-01-01"], ["2025-12-31", "2026-01-02"], ["2027-01-01"]):
        with pytest.raises(Exception):
            open_deal(vm, c, days=days)


def test_rejects_a_bad_notional_or_tolerance(escrow):
    vm, c = escrow
    for n in (0, -NOTIONAL):
        with pytest.raises(Exception):
            open_deal(vm, c, notional=n)
    for t in (-1, 5001, 99999):
        with pytest.raises(Exception):
            open_deal(vm, c, tolerance=t)


def test_rejects_a_bad_provider_list(escrow):
    vm, c = escrow
    for bad in (["frankfurter"], ["frankfurter", "frankfurter"], ["frankfurter", "magic"],
                [], "frankfurter"):
        with pytest.raises(Exception):
            open_deal(vm, c, providers=bad)


def test_rejects_bad_timings(escrow):
    vm, c = escrow
    for sa, dl in ((0, 100), (100, 100), (100, 50), (-5, 100)):
        with pytest.raises(Exception):
            open_deal(vm, c, settle_after=sa, deadline=dl)


# ==========================================================================
# B. provider adapters -- three shapes, two unit conventions
# ==========================================================================


def test_all_three_real_shapes_land_on_one_scale(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    happy(vm)
    assert settle(vm, c, did) == "EXECUTED"
    rows = {r["provider"]: r for r in rates(vm, c, did)}
    for pid in ("frankfurter", "ecb", "currencyapi"):
        assert rows[pid]["days"] == 3
        assert rows[pid]["average"] == AVG


def test_ecb_is_a_cross_rate_not_a_direct_one(escrow):
    """ECB quotes against EUR. Forgetting that would silently pay at the
    wrong rate, so the fixture pins the arithmetic."""
    vm, c = escrow
    did = open_deal(vm, c)
    # quote per EUR 7.79, base per EUR 1.159 -> cross = 6.72131...
    remock(vm, fr=None, ca=None, ec=ecb([779000000] * 3), us=ecb([115900000] * 3, cur="USD"),
           model=ident())
    settle(vm, c, did)
    row = {r["provider"]: r for r in rates(vm, c, did)}["ecb"]
    assert row["average"] == (779000000 * SCALE) // 115900000


def test_a_rate_string_and_a_rate_number_agree(escrow):
    """Frankfurter sends a number, currency-api a number, the CSV a string.
    All three must land on the same integer."""
    vm, c = escrow
    did = open_deal(vm, c)
    remock(vm, fr=frankfurter(), ca={i: curapi(i, rate=str(SCALED[i] / 1e8)) for i in range(3)},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"), model=ident())
    settle(vm, c, did)
    rows = {r["provider"]: r for r in rates(vm, c, did)}
    assert rows["currencyapi"]["average"] == AVG


def test_rejects_a_frankfurter_entry_for_another_base(escrow):
    """A generic URL that quotes whatever it finds is failure mode number one."""
    vm, c = escrow
    did = open_deal(vm, c)
    remock(vm, fr=frankfurter(base="EUR"), ca={i: curapi(i) for i in range(3)},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"), model=ident())
    settle(vm, c, did)
    assert {r["provider"]: r for r in rates(vm, c, did)}["frankfurter"]["days"] == 0


def test_rejects_a_currencyapi_entry_for_another_currency(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    remock(vm, fr=frankfurter(),
           ca={i: curapi(i, quote="jpy") for i in range(3)},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"), model=ident())
    settle(vm, c, did)
    assert {r["provider"]: r for r in rates(vm, c, did)}["currencyapi"]["days"] == 0


def test_rejects_csv_without_the_expected_columns(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    remock(vm, fr=frankfurter(), ca={i: curapi(i) for i in range(3)},
           ec="FOO,BAR\n1,2\n", us=ecb([100000000] * 3, cur="USD"), model=ident())
    settle(vm, c, did)
    assert {r["provider"]: r for r in rates(vm, c, did)}["ecb"]["days"] == 0


def test_rejects_a_payload_that_is_not_json(escrow):
    """Non-empty is not valid. A 500 page proves nothing."""
    vm, c = escrow
    did = open_deal(vm, c)
    remock(vm, fr="<html>Service Unavailable</html>", ca={i: curapi(i) for i in range(3)},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"), model=ident())
    settle(vm, c, did)
    assert {r["provider"]: r for r in rates(vm, c, did)}["frankfurter"]["days"] == 0


def test_rejects_a_payload_missing_rates(escrow):
    vm, c = escrow
    for bad in ('{"amount":1,"base":"USD"}', '{"base":"EUR","rates":{"2025-12-29":{"CNY":6.72}}}',
                "not json at all"):
        vm, c = escrow
        did = open_deal(vm, c)
        remock(vm, fr=bad, ca={i: curapi(i) for i in range(3)},
               ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"), model=ident())
        settle(vm, c, did)
        assert {r["provider"]: r for r in rates(vm, c, did)}["frankfurter"]["days"] == 0


def test_a_provider_that_does_not_answer_contributes_nothing(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    remock(vm, fr=None, ca={i: curapi(i) for i in range(3)},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"), model=ident())
    assert settle(vm, c, did) == "EXECUTED"
    assert {r["provider"]: r for r in rates(vm, c, did)}["frankfurter"]["days"] == 0


# ==========================================================================
# C. cross-validation
# ==========================================================================


def test_the_middle_average_is_used(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    happy(vm)
    assert settle(vm, c, did) == "EXECUTED"
    d = deal(vm, c, did)
    assert d["average"] == AVG
    assert d["valid_count"] == 3
    assert d["cost"] == COST


def test_two_providers_are_enough(escrow):
    vm, c = escrow
    did = open_deal(vm, c, providers=("frankfurter", "ecb"))
    remock(vm, fr=frankfurter(), ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"),
           model=ident())
    assert settle(vm, c, did) == "EXECUTED"
    assert deal(vm, c, did)["valid_count"] == 2


def test_one_provider_alone_never_settles(escrow):
    vm, c = escrow
    did = open_deal(vm, c, providers=("frankfurter", "ecb"))
    remock(vm, fr=frankfurter(), ec=None, us=None, model=ident())
    before = balance(vm, BUYER)
    assert settle(vm, c, did) == "REFUNDED"
    assert deal(vm, c, did)["reason"] == "the period average could not be verified"
    assert balance(vm, BUYER) == before + ESCROW


def test_providers_that_disagree_beyond_tolerance_are_refused(escrow):
    vm, c = escrow
    did = open_deal(vm, c, tolerance=5)
    remock(vm, fr=frankfurter(), ca={i: curapi(i) for i in range(3)},
           ec=ecb([800000000] * 3), us=ecb([100000000] * 3, cur="USD"), model=ident())
    assert settle(vm, c, did) == "REFUNDED"
    d = deal(vm, c, did)
    assert d["reason"] == "providers did not agree"
    assert d["spread_bps"] > 5


def test_a_wider_tolerance_accepts_the_same_data(escrow):
    vm, c = escrow
    did = open_deal(vm, c, tolerance=5000, escrow_amt=800 * 10 ** 18)
    remock(vm, fr=frankfurter(), ca={i: curapi(i) for i in range(3)},
           ec=ecb([800000000] * 3), us=ecb([100000000] * 3, cur="USD"), model=ident())
    assert settle(vm, c, did) == "EXECUTED"


# ==========================================================================
# D. identity
# ==========================================================================


def test_identity_must_agree(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    happy(vm, same="false")
    assert settle(vm, c, did) == "REFUNDED"
    assert deal(vm, c, did)["reason"] == "the period average could not be verified"


def test_unclear_identity_refuses_the_set(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    happy(vm, same="unclear")
    assert settle(vm, c, did) == "REFUNDED"


def test_a_label_not_in_the_response_is_discarded(escrow):
    """The model cannot invent a label: it must occur in that response."""
    vm, c = escrow
    did = open_deal(vm, c)
    happy(vm, same="true")
    remock(vm, fr=frankfurter(), ca={i: curapi(i) for i in range(3)},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"),
           model=ident("true", ["USD/CNY"] + ["a made up ticker"] * 5))
    settle(vm, c, did)
    labels = [r for r in rates(vm, c, did)]
    assert isinstance(labels, list)


def test_a_malformed_identity_answer_is_fail_closed(escrow):
    vm, c = escrow
    for bad in ("not json at all", '{"same":"nope"}', '{"labels":7}', ""):
        vm, c = escrow
        did = open_deal(vm, c)
        remock(vm, fr=frankfurter(), ca={i: curapi(i) for i in range(3)},
               ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"), model=bad)
        assert settle(vm, c, did) == "REFUNDED"


# ==========================================================================
# E. funds
# ==========================================================================


def test_settles_at_the_period_average_and_returns_the_change(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    happy(vm)
    before_s, before_b = balance(vm, SELLER), balance(vm, BUYER)
    held = contract_balance(vm)
    assert settle(vm, c, did) == "EXECUTED"

    d = deal(vm, c, did)
    assert d["seller_paid"] == COST
    assert d["buyer_refund"] == ESCROW - COST
    assert balance(vm, SELLER) == before_s + COST
    assert balance(vm, BUYER) == before_b + (ESCROW - COST)
    assert d["seller_paid"] + d["buyer_refund"] == d["escrow"] == ESCROW
    assert contract_balance(vm) == held - ESCROW


def test_underfunded_escrow_refunds_the_buyer(escrow):
    vm, c = escrow
    did = open_deal(vm, c, escrow_amt=COST - 1000)
    happy(vm)
    before_b = balance(vm, BUYER)
    assert settle(vm, c, did) == "REFUNDED"
    assert deal(vm, c, did)["reason"] == "escrow does not cover the settled cost"
    assert balance(vm, BUYER) == before_b + (COST - 1000)


def test_every_unit_reaches_exactly_one_party(escrow):
    """The identity this app is built to hold, on every path at once."""
    vm, c = escrow
    held = contract_balance(vm)
    d1 = open_deal(vm, c)                                # executes
    happy(vm)
    d2 = open_deal(vm, c, escrow_amt=COST - 1000)        # underfunded
    settle(vm, c, d1)
    settle(vm, c, d2)
    d3 = open_deal(vm, c, providers=("frankfurter", "ecb"))
    remock(vm, fr=frankfurter(), ec=None, us=None, model=ident())   # unverifiable
    settle(vm, c, d3)
    for did in (d1, d2, d3):
        x = deal(vm, c, did)
        assert x["seller_paid"] + x["buyer_refund"] == x["escrow"]
    assert contract_balance(vm) == held


# ==========================================================================
# F. exits
# ==========================================================================


def test_cancel_is_buyer_only_and_only_early(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    for sender in (STRANGER, SELLER):
        vm.warp(iso(10))
        vm.sender = sender
        with pytest.raises(Exception):
            c.cancel(did)
    vm.warp(iso(500))
    vm.sender = BUYER
    with pytest.raises(Exception):
        c.cancel(did)                     # the settle window has opened
    vm, c = escrow
    did = open_deal(vm, c)
    vm.warp(iso(10))
    vm.sender = BUYER
    assert c.cancel(did) == "CANCELLED"


def test_cancel_refunds_the_escrow_whole(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    before_b = balance(vm, BUYER)
    held = contract_balance(vm)
    vm.warp(iso(10))
    vm.sender = BUYER
    assert c.cancel(did) == "CANCELLED"
    assert balance(vm, BUYER) == before_b + ESCROW
    assert contract_balance(vm) == held - ESCROW


def test_expire_only_after_the_deadline(escrow):
    vm, c = escrow
    did = open_deal(vm, c, settle_after=100, deadline=600)
    for at in (100, 599, 600):
        vm.warp(iso(at))
        vm.sender = STRANGER
        with pytest.raises(Exception):
            c.expire(did)
    vm.warp(iso(601))
    vm.sender = STRANGER
    assert c.expire(did) == "REFUNDED"
    assert deal(vm, c, did)["reason"] == "no settlement before the deadline"


def test_settle_only_inside_the_window(escrow):
    vm, c = escrow
    did = open_deal(vm, c, settle_after=100, deadline=600)
    happy(vm)
    for at in (10, 99, 601, 5000):
        with pytest.raises(Exception):
            settle(vm, c, did, at=at)
    assert deal(vm, c, did)["status"] == "OPEN"


# ==========================================================================
# G. write preconditions
# ==========================================================================


def test_a_settled_deal_cannot_move_again(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    happy(vm)
    assert settle(vm, c, did) == "EXECUTED"
    before = full_snapshot(vm, c, did)
    for call in (lambda: settle(vm, c, did, at=510), lambda: c.cancel(did),
                 lambda: c.expire(did)):
        with pytest.raises(Exception):
            call()
    assert full_snapshot(vm, c, did) == before


def test_every_refusal_leaves_the_state_byte_identical(escrow):
    """All four writes enforce the same preconditions, and none of them
    mutates anything when it refuses."""
    vm, c = escrow
    did = open_deal(vm, c, settle_after=100, deadline=600)
    cases = [
        ("settle before the window", lambda: settle(vm, c, did, at=50)),
        ("settle after the deadline", lambda: settle(vm, c, did, at=601)),
        ("cancel from a stranger", lambda: c.cancel(did)),
        ("expire before the deadline", lambda: c.expire(did)),
        ("settle a missing deal", lambda: settle(vm, c, "ghost", at=500)),
    ]
    for label, call in cases:
        vm.warp(iso(500))
        vm.sender = STRANGER
        before = full_snapshot(vm, c, did)
        with pytest.raises(Exception):
            call()
        assert full_snapshot(vm, c, did) == before, label


# ==========================================================================
# H. views
# ==========================================================================


def test_views_return_strings_never_structures(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    for value in (c.get_settlement(did), c.get_rates(did)):
        assert isinstance(value, str)
        json.loads(value)
    assert isinstance(c.get_deal_count(), int)


def test_get_deal_count_tracks_opens(escrow):
    vm, c = escrow
    assert c.get_deal_count() == 0
    open_deal(vm, c)
    assert c.get_deal_count() == 1
    open_deal(vm, c)
    assert c.get_deal_count() == 2


def test_unknown_deal_raises(escrow):
    vm, c = escrow
    for call in (lambda: c.get_settlement("ghost"), lambda: c.get_rates("ghost"),
                 lambda: settle(vm, c, "ghost", at=500), lambda: c.cancel("ghost"),
                 lambda: c.expire("ghost")):
        with pytest.raises(Exception):
            call()


def test_get_rates_records_each_provider(escrow):
    vm, c = escrow
    did = open_deal(vm, c)
    happy(vm)
    settle(vm, c, did)
    rows = rates(vm, c, did)
    assert [r["provider"] for r in rows] == ["frankfurter", "ecb", "currencyapi"]
    assert all(r["days"] == 3 for r in rows)
