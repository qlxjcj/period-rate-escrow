# Period Rate Escrow

**Escrow that settles at the average exchange rate over a closed window.**

Two parties agree a short run of calendar days and a notional. At settlement
the contract pulls each provider's rates for that window, averages them per
provider, refuses the set if the providers disagree beyond the agreed
tolerance, and pays out at the middle average. Unverifiable or over budget —
the whole escrow goes home.

Deployed on GenLayer Bradbury: **`0x54fAFA5377f0c5a4C306eDf0A19eeB6dd5e54e2B`**
([explorer](https://explorer-bradbury.genlayer.com/address/0x54fAFA5377f0c5a4C306eDf0A19eeB6dd5e54e2B))

---

## Why the window is the whole design

An average over a period **cannot exist until the period is over**. So the
contract refuses to settle before the last window day has ended: the evidence
set is final because time made it final, not because a policy note said so.

That is the fix for the most common failure in this class of contract —
deciding while the evidence set can still grow. Here it is impossible.

## Real providers, real units

Three venues, three shapes, **two different unit conventions**:

| provider | shape | currency id | unit |
|---|---|---|---|
| Frankfurter | nested JSON keyed by date | `CNY` | requested base |
| currency-api | flat JSON keyed by code | `cny` | file's base |
| ECB SDW | **CSV** | `CURRENCY=CNY` | **quoted against EUR** |

ECB only publishes against EUR, so a USD→CNY rate has to be **formed as a cross
rate** from two series. Forgetting that would silently pay at the wrong rate —
which is exactly the class of bug the "units must be right" rule exists for.

Nothing is accepted for being non-empty. A payload must parse in its own
format, name the requested currency, cover the window days, and carry a
positive rate. A 500 page, a CSV missing `TIME_PERIOD`, a quote for another
base — all refused, with a recorded reason.

**No floats touch a rate.** Decimals become integers at `1e8` scale by string
arithmetic, because a float in that path rounds differently across validators
and silently breaks consensus on the stored value.

## No model decides anything

There is **no model in this contract**. Every source is verified in code for the
currency it names — Frankfurter's `base` field, currency-api's key, the ECB's
`CURRENCY` column — so a wrong-currency payload is refused before any arithmetic.

An earlier revision asked a model to confirm the pair. On live data that gate
refused a correct settlement: the ECB quotes `CNY/EUR`, so a model answering
"this does not quote USD/CNY" was **right**, and the escrow refunded. The
question had a deterministic answer that code already had. The gate was removed.

## Consensus

Fetching and parsing share **one equivalence-principle block**
(`run_nondet_unsafe`), so a settlement has a single consensus point. Decision
fields — `verdict`, `valid`, which sources covered the window — are compared
**exactly**; the period average is compared **within a tolerance**, because a
live feed moves between two fetches and exact equality on a moving number can
never converge.

## Money

`cost = notional * average / 1e8`. Then:

| outcome | seller | buyer |
|---|---|---|
| verified, cost fits | `cost` | `escrow - cost` (the change) |
| providers disagree, or unverifiable | — | the whole escrow |
| escrow does not cover the cost | — | the whole escrow |
| cancelled before the window opens | — | the whole escrow |
| deadline passes with no settlement | — | the whole escrow |

`seller_paid + buyer_refund == escrow` on every path.

## App

`index.html` is a working app: open a settlement, browse them, inspect one and
settle, cancel or expire it. Every read is bound to a settlement id — there is
no `get_latest()` anywhere. Wallet writes go through the injected provider,
with loading states and surfaced errors. DOM APIs only.

## API

| method | note |
|---|---|
| `open_settlement(seller, base, quote, days, notional, tolerance_bps, providers, settle_after, deadline)` | **payable**. `days` are `YYYY-MM-DD`, all already past |
| `settle(deal_id)` | averages each provider over the closed window and pays |
| `cancel(deal_id)` | buyer only, before the settle window opens |
| `expire(deal_id)` | anyone, after the deadline |
| `get_settlement` / `get_rates` / `get_deal_count` | JSON strings and plain ints |

**Bounds** — 1..5 window days, 2..3 providers, tolerance 0..5000 bps.

## Testing

37 direct-mode tests in `tests/direct/test_period_rate_escrow.py`, in eight
groups: deal creation, provider adapters, cross-validation, the no-model
refusals, funds, exits, write preconditions, views. The payloads in
`conftest.py` are the real
shapes captured from the three venues. Two accounting hooks make every payout
assertion a real balance change. There is deliberately no catch-all model mock.

```
python -m pytest tests/direct -q        # 37 passed
```

## Source integrity

```
sha256  bc5f35f5182bff56fc8839f963cf045cf3c8038479f11472b2aa4ab23b64c68c
```
