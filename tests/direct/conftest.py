"""Fixtures for Period Rate Escrow direct-mode tests.

One unit model, stated once and used everywhere:

    AMOUNT  native GEN, integer wei                        (10**18)
    RATE    quote per base, integer at 10**8               (10**8)
    payout  = notional_wei * average_rate // strike_rate   (wei)

The payloads below are the real shapes captured from Frankfurter, currency-api
and the ECB Statistical Data Warehouse: nested JSON keyed by date, flat JSON
keyed by lowercase currency code, and CSV whose rates are quoted against EUR so
a cross rate has to be formed.

Direct mode does not track native value flow, so two accounting hooks are
installed so every payout assertion is a real balance change, and a refused
payable call restores the value.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "period_rate_escrow.py",
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
RATE_SCALE = 10 ** 8

BUYER = "0x" + "aa" * 20
SELLER = "0x" + "bb" * 20
STRANGER = "0x" + "44" * 20
BAD_ADDR = "0x1234"

DAYS = ["2025-12-29", "2025-12-30", "2025-12-31"]
# One rate per day, identical across the three venues so every expected number
# below is hand-checkable.
RATES = [6.72, 6.7215, 6.7191]
SCALED = [672000000, 672150000, 671910000]
AVG = sum(SCALED) // 3                      # 672020000

NOTIONAL = 100 * 10 ** 18                   # 100 GEN of principal, in wei
ESCROW = 700 * 10 ** 18                     # 700 GEN of collateral, in wei
STRIKE = 672000000                          # 6.72 quote per base at 1e8
PAYOUT = (NOTIONAL * AVG) // STRIKE         # wei, by the one formula

NOW = 500


def iso(offset: int) -> str:
    return (BASE + timedelta(seconds=offset)).isoformat()


def balance(vm, addr):
    return vm._balances.get(vm._to_bytes(addr), 0)


def contract_balance(vm):
    return vm._balances.get(vm._contract_address, 0)


# ---------------- real provider payload shapes ----------------

def frankfurter(rates=None, base="USD", quote="CNY", days=None):
    rs = rates if rates is not None else RATES
    ds = days if days is not None else DAYS
    return json.dumps({"amount": 1.0, "base": base, "start_date": ds[0],
                       "end_date": ds[-1],
                       "rates": {ds[i]: {quote: rs[i]} for i in range(len(ds))}})


def curapi(day_index, rate=None, base="usd", quote="cny"):
    r = rate if rate is not None else RATES[day_index]
    return json.dumps({"date": DAYS[day_index], base: {quote: r}})


def ecb(values, cur="CNY"):
    """ECB CSV: rates are quoted against EUR, so a cross rate is formed."""
    head = ("KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,"
            "OBS_VALUE,OBS_STATUS,UNIT_INDEX_BASE,TITLE")
    rows = ["EXR.D." + cur + ".EUR.SP00.A,M," + cur + ",EUR,SP00,A," + DAYS[i] +
            "," + str(values[i]) + ",A,,ECB reference rate" for i in range(len(DAYS))]
    return head + "\n" + "\n".join(rows) + "\n"


def remock(vm, fr=None, ca=None, ec=None, us=None):
    """Install exactly the provider answers the test expects."""
    vm.clear_mocks()
    if fr is not None:
        vm.mock_web(".*api\\.frankfurter\\.app.*", {"method": "GET", "status": 200, "body": fr})
    for i in range(len(DAYS)):
        body = (ca or {}).get(i)
        if body is not None:
            vm.mock_web(".*currency-api@" + DAYS[i] + ".*",
                        {"method": "GET", "status": 200, "body": body})
    if ec is not None:
        vm.mock_web(".*EXR/D\\.CNY\\.EUR.*", {"method": "GET", "status": 200, "body": ec})
    if us is not None:
        vm.mock_web(".*EXR/D\\.USD\\.EUR.*", {"method": "GET", "status": 200, "body": us})


def happy(vm, fr=None, ca=None):
    remock(vm, fr=fr if fr is not None else frankfurter(),
           ca=ca if ca is not None else {i: curapi(i) for i in range(len(DAYS))},
           ec=ecb(SCALED), us=ecb([100000000] * 3, cur="USD"))


@pytest.fixture
def escrow(direct_vm, direct_deploy):
    vm = direct_vm

    def _value_transfer_hook(vm, request):
        if "PostMessage" not in request:
            return None
        msg = request["PostMessage"]
        amount = int(msg.get("value", 0))
        if amount > 0:
            contract = vm._contract_address
            recipient = vm._to_bytes(msg["address"])
            vm._balances[contract] = vm._balances.get(contract, 0) - amount
            vm._balances[recipient] = vm._balances.get(recipient, 0) + amount
        return {"ok": None}

    vm._gl_call_hook = _value_transfer_hook

    remock(vm)
    c = direct_deploy(CONTRACT)

    def _wrap(fn):
        def inner(*args):
            paid = 0
            sender = vm._to_bytes(vm.sender)
            contract = vm._contract_address
            if vm.value > 0:
                vm._balances[sender] = vm._balances.get(sender, 0) - vm.value
                vm._balances[contract] = vm._balances.get(contract, 0) + vm.value
                paid = vm.value
            try:
                return fn(*args)
            except Exception:
                # A refused payable call moves nothing on chain either.
                if paid:
                    vm._balances[sender] = vm._balances.get(sender, 0) + paid
                    vm._balances[contract] = vm._balances.get(contract, 0) - paid
                raise
        return inner

    c.open_settlement = _wrap(c.open_settlement)

    for addr in (BUYER, SELLER, STRANGER):
        vm.deal(addr, 1000 * ESCROW)

    vm.warp(iso(0))
    return vm, c


def open_deal(vm, c, seller=SELLER, base="USD", quote="CNY", days=None,
              notional=NOTIONAL, strike=STRIKE, tolerance=500,
              providers=("frankfurter", "ecb", "currencyapi"),
              settle_after=100, deadline=10000, escrow_amt=ESCROW, sender=BUYER):
    vm.warp(iso(0))
    vm.sender = sender
    vm.value = escrow_amt
    try:
        return c.open_settlement(seller, base, quote,
                                 list(days) if days is not None else list(DAYS),
                                 notional, strike, tolerance, list(providers),
                                 settle_after, deadline)
    finally:
        vm.value = 0


def settle(vm, c, did, at=NOW, sender=STRANGER):
    vm.warp(iso(at))
    vm.sender = sender
    return c.settle(did)


def deal(vm, c, did):
    return json.loads(c.get_settlement(did))


def rates(vm, c, did):
    return json.loads(c.get_rates(did))


def full_snapshot(vm, c, did):
    return (c.get_settlement(did), c.get_rates(did))


def row(provider, days, covered, average):
    return {"provider": provider, "days": days, "covered": covered, "average": average}


def outcome(rows, verdict="VERIFIED", valid=None, spread=0, average=0, payout=0):
    """A run() result in the exact shape validators compare."""
    return {"rows": rows, "valid": len(rows) if valid is None else valid,
            "spread_bps": spread, "average": average, "payout": payout,
            "verdict": verdict}
