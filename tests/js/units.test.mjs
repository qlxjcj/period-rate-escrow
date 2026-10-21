import test from "node:test";
import assert from "node:assert/strict";

import {
  AMOUNT_DECIMALS,
  RATE_DECIMALS,
  coverage,
  formatAmount,
  formatRate,
  payoutWei,
  toRate,
  toUnits,
  toWei,
} from "../../units.mjs";

const E18 = 10n ** 18n;
const E8 = 10n ** 8n;

test("the scales are the ones the contract uses", () => {
  assert.equal(AMOUNT_DECIMALS, 18);
  assert.equal(RATE_DECIMALS, 8);
});

test("GEN text becomes wei, not 1e8 units", () => {
  // This is the bug the Steward caught: the old helper scaled amounts by 1e8.
  assert.equal(toWei("1"), E18);
  assert.notEqual(toWei("1"), E8);
  assert.equal(toWei("0.1"), E18 / 10n);
  assert.equal(toWei("0.000000000000000001"), 1n);
  assert.equal(toWei("12.5"), 12n * E18 + E18 / 2n);
  assert.equal(toWei("  3  "), 3n * E18);
  assert.equal(toWei("0"), 0n);
});

test("an amount with more than 18 decimals is refused, not rounded", () => {
  assert.throws(() => toWei("0.0000000000000000001"), /At most 18 decimals/);
  assert.throws(() => toWei("1.1234567890123456789"), /At most 18 decimals/);
});

test("garbage and negatives never become money", () => {
  for (const bad of ["", "-1", "1e18", "0x10", "1,000", ".", "abc", "1.2.3", " "]) {
    assert.throws(() => toWei(bad), Error, "should refuse " + JSON.stringify(bad));
  }
});

test("a rate keeps 8 decimals", () => {
  assert.equal(toRate("6.7202"), 672020000n);
  assert.equal(toRate("6.72"), 672000000n);
  assert.equal(toRate("6.7"), 670000000n);
  assert.equal(toRate("160"), 160n * E8);
  assert.throws(() => toRate("6.7202000001"), /At most 8 decimals/);
});

test("the payout is the same integer the contract computes", () => {
  const notional = 100n * E18;
  const strike = 672000000n;
  const avg = 672020000n;
  assert.equal(payoutWei(notional, avg, strike), (notional * avg) / strike);
  assert.equal(payoutWei(notional, strike, strike), notional);
  assert.equal(payoutWei(notional, strike * 2n, strike), notional * 2n);
  assert.equal(payoutWei(1000n, avg, 10n ** 12n), 0n);
});

test("a payout with no strike is refused", () => {
  assert.throws(() => payoutWei(E18, 672020000n, 0n), /strike/);
  assert.throws(() => payoutWei(E18, 672020000n, -1n), /strike/);
});

test("amounts print as GEN with 6 decimals by default", () => {
  assert.equal(formatAmount(E18), "1");
  assert.equal(formatAmount(E18 / 10n), "0.1");
  assert.equal(formatAmount(1234567890123456789n), "1.234567");  // truncated, never rounded up
  assert.equal(formatAmount(0n), "0");
  assert.equal(formatAmount(1234567n * E18), "1,234,567");
  assert.equal(formatAmount(1n, 18), "0.000000000000000001");
});

test("rates print with the decimals they actually carry", () => {
  assert.equal(formatRate(672020000n), "6.7202");
  assert.equal(formatRate(672000000n), "6.72");
  assert.equal(formatRate(160n * E8), "160");
  assert.equal(formatRate(0n), "0");
});

test("format then parse is a round trip at display precision", () => {
  for (const wei of [0n, 1n, 10n ** 17n, 1234567890123456789n, 700n * E18]) {
    const shown = formatAmount(wei, 18);
    assert.equal(toWei(shown.replace(/,/g, "")), wei);
  }
});

test("coverage reads as found over needed", () => {
  assert.equal(coverage(3, 3), "3/3");
  assert.equal(coverage(2, 3), "2/3");
});
