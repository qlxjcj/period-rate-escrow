# FixSpan Escrow

**Settle at the average exchange rate over a closed window — a rate that does
not exist until the window is over.**

Two parties agree a short run of calendar days, a principal and a strike. At
settlement the contract pulls each provider's rates for that window, averages
them per provider, refuses the set if the providers disagree beyond the agreed
tolerance, and pays at the middle average. Unverifiable, or over budget — the
whole escrow goes home.

Deployed on GenLayer Bradbury: **`0xeb7657fe706C9e699fFaab4E71F63786234a9Fdf`**
([explorer](https://explorer-bradbury.genlayer.com/address/0xeb7657fe706C9e699fFaab4E71F63786234a9Fdf))

---

## The unit model (one asset, one scale per kind of number)

This is the corrected model, and it is the same in the contract, the client, the
display and the tests.

| quantity | asset | representation | where |
|---|---|---|---|
| escrow | **GEN** | integer **wei** (10¹⁸) | `msg.value`, storage, `emit_transfer` |
| notional | **GEN** | integer **wei** (10¹⁸) | `open_settlement` argument |
| strike | rate | integer at **10⁸**, quote per base | `open_settlement` argument |
| observed rate | rate | integer at **10⁸**, quote per base | provider parsers |
| payout | **GEN** | integer **wei** | `payout = notional * average // strike` |

The rate is **only ever used as a ratio**. Both `average` and `strike` are quote
per base, so `average / strike` is dimensionless and the payout stays in wei. The
contract never converts GEN into a currency and never claims to; an FX rate is
not money.

Two guards come straight out of that model:

- a **strike outside 0.0001…10000 at 1e8 is refused**, because `6.72` where
  `672000000` was meant is the classic unit bug (and it would silently pay 1e8
  times too much or too little);
- the **client converts with the same two scales**: `toWei` (18 decimals) for
  amounts, `toRate` (8 decimals) for rates. The old page scaled *everything* by
  1e8, so a notional of `100` reached the chain as `1e10` wei instead of `1e20`.

`get_settlement` returns a `units` block on every deal
(`{"asset":"GEN","amount_decimals":18,"rate_decimals":8,"rate":"quote per base"}`)
so the model is visible on chain, not just in this file.

## Why the window is the whole design

An average over a period **cannot exist until the period is over**. So the
contract refuses to settle before the last window day has ended: the evidence set
is final because time made it final, not because a policy note said so.

## Providers: real shapes, and exact coverage

Three venues, three shapes, **two different unit conventions**:

| provider | shape | currency id | unit |
|---|---|---|---|
| Frankfurter | nested JSON keyed by date | `CNY` | requested base |
| currency-api | flat JSON keyed by code | `cny` | file's base |
| ECB SDW | **CSV** | `CURRENCY=CNY` | **quoted against EUR** |

ECB only publishes against EUR, so a USD→CNY rate is formed as a **cross rate**
from two series.

**A provider counts only when it covers every requested day.** Each row reports
`days` found, `need` requested and `covered`; the average is computed over
providers where `covered` is true. A 2-of-3 answer is not the period average and
does not enter the median — the page shows `2/3` in red, and the settlement falls
back to the providers that did cover the window (or to a refund if fewer than
two remain).

Nothing is accepted for being non-empty: a payload must parse in its own format,
name the requested currency, cover the window and carry a positive rate. A 500
page, a CSV missing `TIME_PERIOD`, a quote for another base — all refused, with a
recorded reason.

**No floats touch a rate.** Decimals become integers at `1e8` by string
arithmetic, because a float here rounds differently on different validators.

## Consensus binds the payout

Fetching and parsing share one equivalence block (`run_nondet_unsafe`). Two
honest validators may fetch at slightly different moments, so the comparison is
tolerant — but the tolerance is **deal-appropriate** and every number the payout
depends on is bound:

| bound | how |
|---|---|
| verdict, valid count | exactly |
| per-provider coverage flag and day count | exactly |
| per-provider period average | within `tol` |
| observed spread | within `tol` |
| final period average | within `tol` |
| **payout** | within `tol` |

`tol = min(max(tolerance_bps, 10), 250)` — floored at 10 bps so a live feed can
jitter, capped at 250 bps so a loose deal cannot wave through numbers that
change the payout, and never wider than the deal's own spread tolerance in
effect.

An earlier revision compared only the verdict, the valid count and the average,
and reduced each provider row to "did it answer at all". Two validators could
therefore agree while a provider's average and the **payout** differed. That is
what `_agree` now refuses, and it is covered by twelve tests.

## No model decides anything

There is **no model in this contract**. Every source is verified in code for the
currency it names — Frankfurter's `base` field, currency-api's key, the ECB's
`CURRENCY` column — so a wrong-currency payload is refused before any arithmetic.

An earlier revision asked a model to confirm the pair. On live data that gate
refused a correct settlement: the ECB quotes `CNY/EUR`, so a model answering
"this does not quote USD/CNY" was **right**, and the escrow refunded. The
question had a deterministic answer that code already had. The gate was removed.

## Money

`payout = notional * average // strike`, in wei. Then:

| outcome | seller | buyer |
|---|---|---|
| verified, payout fits | `payout` | `escrow - payout` (the change) |
| providers disagree, or fewer than two cover the window | — | the whole escrow |
| payout rounds to zero | — | the whole escrow |
| payout exceeds the escrow | — | the whole escrow |
| cancelled before the window opens | — | the whole escrow |
| deadline passes with no settlement | — | the whole escrow |

`seller_paid + buyer_refund == escrow` on every path.

## App

`index.html` is a working app: open a settlement, browse them, inspect one and
settle, cancel or expire it. Amounts are entered and displayed as **GEN**, rates
as **quote per base**; the form shows the payout at the strike before you send.
Every read is bound to a settlement id — there is no `get_latest()` anywhere.
Writes go through the injected provider, with loading states and surfaced
errors. DOM APIs only.

The conversion lives in **`units.mjs`** so the browser and the tests run the same
arithmetic:

```
toWei("0.1")        === 100000000000000000n
toRate("6.7202")    === 672020000n
payoutWei(1e17, 672020000n, 660000000n) === 101821212121212121n
```

## API

| method | note |
|---|---|
| `open_settlement(seller, base, quote, days, notional, strike, tolerance_bps, providers, settle_after, deadline)` | **payable**. `notional` in wei, `strike` at 1e8, `days` all already past |
| `settle(deal_id)` | averages each covering provider over the closed window and pays the ratio |
| `cancel(deal_id)` | buyer only, before the settle window opens |
| `expire(deal_id)` | anyone, after the deadline |
| `get_settlement` / `get_rates` / `get_deal_count` | JSON strings and plain ints |

**Bounds** — 1..5 window days, 2..3 providers, tolerance 0..5000 bps,
strike 1e4..1e12 at 1e8.

## Testing

```
python -m pytest tests -q                     # 65 contract + 1 browser wrapper
node --test tests/js/units.test.mjs           # 11 browser-conversion tests
```

- `tests/direct/test_period_rate_escrow.py` — 65 tests in eight groups: the unit
  model, provider adapters, exact coverage, cross-validation, validator
  agreement, funds, exits, preconditions. The payloads in `conftest.py` are the
  real shapes captured from the three venues, and two accounting hooks make every
  payout assertion a real balance change.
- `tests/js/units.test.mjs` — the browser conversion: `toWei` is 1e18 (and no
  longer 1e8), rates keep 8 decimals, garbage and negatives never become money,
  and the JS payout is bit-identical to the contract's integer division.
- `tests/test_browser_units.py` runs the JS tests under `pytest` when node is
  present.

## Source integrity

```
sha256  0707cb52ff8641e11c6e3650194831d39e78876f5b1ba2333c85fe8179caca48
bytes   20002
```
