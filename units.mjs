// units.mjs — the browser half of the contract's unit model.
//
//   AMOUNT  native GEN, integer wei      10**18
//   RATE    quote per base, integer 1e8  10**8
//   payout  = notional_wei * average // strike     (wei)
//
// Every function here is exact integer arithmetic on BigInt. Nothing in this
// file touches Number, so nothing in this file can round.

export const AMOUNT_DECIMALS = 18;
export const RATE_DECIMALS = 8;

const pow10 = (n) => 10n ** BigInt(n);

/** Parse a non-negative decimal string into an integer at `decimals`. */
export function toUnits(text, decimals) {
  const s = String(text).trim();
  const m = /^(\d*)(?:\.(\d*))?$/.exec(s);
  if (!m || (m[1] === "" && (m[2] === undefined || m[2] === ""))) {
    throw new Error("Not a non-negative decimal: " + text);
  }
  const whole = m[1] || "0";
  const frac = m[2] || "";
  if (frac.length > decimals) {
    throw new Error("At most " + decimals + " decimals: " + text);
  }
  return BigInt(whole) * pow10(decimals) + BigInt((frac + "0".repeat(decimals)).slice(0, decimals));
}

/** GEN text -> wei. */
export const toWei = (text) => toUnits(text, AMOUNT_DECIMALS);

/** Rate text (quote per base) -> integer at 1e8. */
export const toRate = (text) => toUnits(text, RATE_DECIMALS);

/** wei out. The ratio average/strike is dimensionless, exactly like on chain. */
export function payoutWei(notionalWei, averageRate, strikeRate) {
  const n = BigInt(notionalWei);
  const avg = BigInt(averageRate);
  const strike = BigInt(strikeRate);
  if (strike <= 0n) throw new Error("strike must be a positive rate at 1e8");
  if (avg <= 0n) return 0n;
  return (n * avg) / strike;
}

function withThousands(digits) {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** Integer at `decimals` -> display string, trailing zeros trimmed. */
export function formatUnits(value, decimals, dp) {
  const v = BigInt(value);
  const scale = pow10(decimals);
  const neg = v < 0n;
  const a = neg ? -v : v;
  const whole = a / scale;
  const keep = Math.max(0, Math.min(dp === undefined ? decimals : dp, decimals));
  const frac = (a % scale).toString().padStart(decimals, "0").slice(0, keep).replace(/0+$/, "");
  const sign = neg ? "-" : "";
  return frac ? sign + withThousands(whole.toString()) + "." + frac : sign + withThousands(whole.toString());
}

export const formatAmount = (wei, dp) => formatUnits(wei, AMOUNT_DECIMALS, dp === undefined ? 6 : dp);
export const formatRate = (rate, dp) => formatUnits(rate, RATE_DECIMALS, dp === undefined ? 6 : dp);

/** Coverage text for a provider row: "3/3" or "2/3". */
export const coverage = (found, need) => String(found) + "/" + String(need);
