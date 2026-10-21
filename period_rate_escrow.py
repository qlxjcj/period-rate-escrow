# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
import json
from genlayer import *

# ---------------- the unit model ----------------
# AMOUNT: the escrowed native asset (GEN), integer wei, 10**18.
# RATE:   quote per base, integer at 10**8.
# The rate is only ever a ratio: payout_wei = notional_wei * average // strike.
# Both rates are quote per base, so the ratio is dimensionless and the payout
# stays in wei. The contract never converts GEN into a currency.

RATE_SCALE = 10 ** 8
AGREE_MIN = 10              # validator agreement floor, bps (feed jitter)
AGREE_MAX = 250             # validator agreement cap, bps

OPEN = "OPEN"
EXECUTED = "EXECUTED"
REFUNDED = "REFUNDED"
CANCELLED = "CANCELLED"


class PeriodRateEscrow(gl.Contract):
    """Escrow that settles at the average exchange rate over a closed window."""

    deals: TreeMap[str, str]
    rates: TreeMap[str, str]
    deal_count: u256

    def __init__(self):
        self.deal_count = 0

    # ---------------- helpers ----------------

    def _addr_hex(self, a) -> str:
        if isinstance(a, str):
            return a
        h = getattr(a, "as_hex", None)
        if isinstance(h, str):
            return h
        if isinstance(a, (bytes, bytearray)):
            return "0x" + bytes(a).hex()
        return str(a)

    def _iso_to_epoch(self, s: str) -> int:
        s = s.strip()
        if len(s) < 19:
            return 0
        offset = 0
        tail = s[19:]
        if tail.endswith("Z"):
            tail = tail[:-1]
        if tail and (tail[0] == "+" or tail[0] == "-"):
            try:
                offset = (1 if tail[0] == "+" else -1) * (int(tail[1:3]) * 3600 + int(tail[4:6]) * 60)
            except (ValueError, IndexError):
                offset = 0
        try:
            y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
            hh, mm, ss = int(s[11:13]), int(s[14:16]), int(s[17:19])
        except (ValueError, IndexError):
            return 0
        if not (1 <= mo <= 12 and 1 <= d <= 31 and hh < 24 and mm < 60 and ss < 61):
            return 0
        yy = y - 1 if mo <= 2 else y
        era = yy // 400
        yoe = yy - era * 400
        doy = (153 * (mo + (-3 if mo > 2 else 9)) + 2) // 5 + d - 1
        doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
        return (era * 146097 + doe - 719468) * 86400 + hh * 3600 + mm * 60 + ss - offset

    def _now(self) -> int:
        raw = getattr(gl, "message_raw", None)
        d = None
        if raw is not None:
            try:
                d = raw["datetime"]
            except Exception:
                d = None
        if d is None:
            d = getattr(gl.message, "datetime", None)
        if d is None:
            return 0
        return self._iso_to_epoch(d if isinstance(d, str) else str(d))

    def _dec(self, text) -> int:
        """Decimal literal -> integer at RATE_SCALE. String arithmetic only:
        floats round differently on different validators."""
        s = str(text).strip()
        if not s or "e" in s or "E" in s:
            return 0
        neg = s.startswith("-")
        if neg:
            s = s[1:]
        if "." in s:
            whole, frac = s.split(".", 1)
        else:
            whole, frac = s, ""
        if whole == "" and frac == "":
            return 0
        if not (whole or "0").isdigit() or not (frac or "0").isdigit():
            return 0
        if whole == "":
            whole = "0"
        v = int(whole) * RATE_SCALE + int((frac + "00000000")[:8])
        return -v if neg else v

    def _body(self, content) -> str:
        b = getattr(content, "body", None)
        if b is None:
            return str(content)
        return b.decode("utf-8", errors="replace") if isinstance(b, bytes) else str(b)

    def _transfer(self, to: Address, amount: int):
        if amount > 0:
            gl.get_contract_at(to).emit_transfer(value=u256(amount))

    def _load(self, did: str) -> dict:
        raw = self.deals.get(str(did), "")
        if not raw:
            raise gl.vm.UserError("Settlement not found")
        return json.loads(raw)

    def _save(self, d: dict):
        self.deals[d["deal_id"]] = json.dumps(d)

    def _payout(self, notional: int, average: int, strike: int) -> int:
        if strike <= 0 or average <= 0:
            return 0
        return (notional * average) // strike

    def _agree_tol(self, tolerance_bps: int) -> int:
        # Deal-appropriate: the deal's own tolerance, floored for feed jitter,
        # capped so a loose deal cannot wave through a different payout.
        t = int(tolerance_bps)
        if t < AGREE_MIN:
            t = AGREE_MIN
        if t > AGREE_MAX:
            t = AGREE_MAX
        return t

    def _agree(self, his, mine, tol: int, providers: int) -> bool:
        # Every payout-determining number is bound: verdict, valid count, the
        # per-provider coverage and average, the spread, the average, the payout.
        if not isinstance(his, dict) or not isinstance(mine, dict):
            return False
        if str(his.get("verdict", "")) != str(mine.get("verdict", "")):
            return False
        if str(his.get("valid", "")) != str(mine.get("valid", "")):
            return False
        hs, ms = int(his.get("spread_bps", 0)), int(mine.get("spread_bps", 0))
        if hs < 0 or ms < 0 or abs(hs - ms) > tol:
            return False
        for k in ("average", "payout"):
            h, m = int(his.get(k, 0)), int(mine.get(k, 0))
            if h <= 0 or m <= 0:
                if h != m:
                    return False
            elif abs(h - m) * 10000 > h * tol:
                return False
        hr, mr = his.get("rows"), mine.get("rows")
        if not isinstance(hr, list) or not isinstance(mr, list):
            return False
        if len(hr) != providers or len(mr) != providers:
            return False
        for x, y in zip(hr, mr):
            if not isinstance(x, dict) or not isinstance(y, dict):
                return False
            if str(x.get("provider")) != str(y.get("provider")):
                return False
            if str(x.get("days")) != str(y.get("days")):
                return False
            if bool(x.get("covered")) != bool(y.get("covered")):
                return False
            h, m = int(x.get("average", 0)), int(y.get("average", 0))
            if h <= 0 or m <= 0:
                if h != m:
                    return False
            elif abs(h - m) * 10000 > h * tol:
                return False
        return True

    # ---------------- provider adapters ----------------
    # Parsing is pure. Every fetch happens inside the single equivalence block
    # in settle(), where validators redo it and the lint can see it.

    def _csv_col(self, head: list, name: str) -> int:
        for i in range(len(head)):
            if head[i].strip() == name:
                return i
        return -1

    def _p_frankfurter(self, body: str, days: list, base: str, quote: str) -> dict:
        out = {}
        try:
            data = json.loads(body)
        except Exception:
            return out
        rates = data.get("rates")
        if not isinstance(rates, dict):
            return out
        if str(data.get("base", "")).upper() != base.upper():
            return out
        for day in days:
            node = rates.get(day)
            if isinstance(node, dict) and quote.upper() in node:
                out[day] = self._dec(node[quote.upper()])
        return out

    def _p_currencyapi(self, body: str, day: str, base: str, quote: str) -> dict:
        try:
            data = json.loads(body)
        except Exception:
            return {}
        node = data.get(base.lower())
        if not isinstance(node, dict):
            return {}
        if quote.lower() not in node:
            return {}
        v = self._dec(node[quote.lower()])
        return {day: v} if v > 0 else {}

    def _p_ecb(self, body: str, days: list) -> dict:
        out = {}
        lines = str(body).replace("\r\n", "\n").split("\n")
        if len(lines) < 2:
            return out
        head = lines[0].split(",")
        ci, vi = self._csv_col(head, "TIME_PERIOD"), self._csv_col(head, "OBS_VALUE")
        if ci < 0 or vi < 0:
            return out
        for line in lines[1:]:
            cells = line.split(",")
            if len(cells) <= max(ci, vi):
                continue
            day = cells[ci].strip()
            if day in days:
                out[day] = self._dec(cells[vi].strip())
        return out

    def _pair(self, ev: dict) -> dict:
        # Two EUR-based series combine into the requested pair.
        q, b = ev.get("ecb_quote", {}), ev.get("ecb_base", {})
        out = {}
        for day in q:
            if day in b and b[day] > 0 and q[day] > 0:
                out[day] = (q[day] * RATE_SCALE) // b[day]
        return out

    # ---------------- lifecycle ----------------

    @gl.public.write.payable
    def open_settlement(self, seller: str, base: str, quote: str, days: list,
                        notional: int, strike: int, tolerance_bps: int, providers: list,
                        settle_after: int, deadline: int) -> str:
        """Escrow GEN, freeze the window, the pair, the strike and the sources."""
        buyer = self._addr_hex(gl.message.sender_address)
        escrow = int(gl.message.value)
        ok = isinstance(seller, str) and seller.startswith("0x") and len(seller) == 42
        if ok:
            try:
                int(seller[2:], 16)
            except ValueError:
                ok = False
        if not ok or self._addr_hex(seller).lower() == buyer.lower():
            raise gl.vm.UserError("Seller must be another 0x address")
        if escrow <= 0:
            raise gl.vm.UserError("Escrow must be > 0 wei")
        b = str(base).strip().upper()
        q = str(quote).strip().upper()
        if len(b) != 3 or len(q) != 3 or not b.isalpha() or not q.isalpha() or b == q:
            raise gl.vm.UserError("Base and quote must differ")
        if not isinstance(days, list) or not days or len(days) > 5:
            raise gl.vm.UserError("Window must be 1..5 days")
        wdays = []
        for d in days:
            ds = str(d).strip()
            if len(ds) != 10 or ds[4] != "-" or ds[7] != "-" or ds in wdays:
                raise gl.vm.UserError("Days must be distinct YYYY-MM-DD")
            wdays.append(ds)
        wdays.sort()
        n = int(notional)
        if n <= 0:
            raise gl.vm.UserError("Notional must be > 0 wei")
        st = int(strike)
        # A mis-scaled strike is the classic unit bug: 6.72 not 672000000.
        if st < 10 ** 4 or st > 10 ** 12:
            raise gl.vm.UserError("Strike must be a rate at 1e8, 0.0001..10000")
        if not 0 <= int(tolerance_bps) <= 5000:
            raise gl.vm.UserError("Tolerance must be 0..5000 bps")
        if not isinstance(providers, list) or len(providers) < 2:
            raise gl.vm.UserError("Need at least two providers")
        plist = []
        for p in providers:
            pid = str(p).strip()
            if pid not in ("frankfurter", "ecb", "currencyapi") or pid in plist:
                raise gl.vm.UserError("Providers must be distinct and known")
            plist.append(pid)
        now = self._now()
        if int(settle_after) <= 0 or int(deadline) <= int(settle_after):
            raise gl.vm.UserError("Deadline must follow settle_after")
        # An average cannot exist before the window ends.
        for d in wdays:
            end = self._iso_to_epoch(d + "T23:59:59+00:00")
            if end <= 0 or end >= now:
                raise gl.vm.UserError("Every window day must be real and past")

        did = "d" + str(int(self.deal_count))
        self.deals[did] = json.dumps({
            "deal_id": did, "buyer": buyer, "seller": self._addr_hex(seller),
            "base": b, "quote": q, "days": wdays,
            "notional": n, "escrow": escrow, "strike": st,
            "tolerance_bps": int(tolerance_bps), "providers": plist,
            "settle_after": now + int(settle_after), "deadline": now + int(deadline),
            "status": OPEN, "reason": "",
            "average": 0, "payout": 0, "seller_paid": 0, "buyer_refund": 0,
            "valid_count": 0, "spread_bps": 0,
            "units": {"asset": "GEN", "amount_decimals": 18,
                      "rate_decimals": 8, "rate": "quote per base"},
        })
        self.deal_count = u256(int(self.deal_count) + 1)
        return did

    @gl.public.write
    def settle(self, deal_id: str) -> str:
        """Average each provider over the closed window and pay the ratio."""
        d = self._load(deal_id)
        if str(d["status"]) != OPEN:
            raise gl.vm.UserError("Settlement is " + str(d["status"]))
        now = self._now()
        if now < int(d["settle_after"]):
            raise gl.vm.UserError("Settle window has not opened")
        if now > int(d["deadline"]):
            raise gl.vm.UserError("Settle window has closed")
        days = [str(x) for x in d["days"]]
        b, q = str(d["base"]), str(d["quote"])
        need = len(days)
        first, last = days[0], days[-1]
        notional, strike = int(d["notional"]), int(d["strike"])

        urls = []
        for pid in d["providers"]:
            if pid == "frankfurter":
                urls.append((pid, "range", "", "https://api.frankfurter.app/" + first +
                             ".." + last + "?from=" + b + "&to=" + q))
            elif pid == "currencyapi":
                for day in days:
                    urls.append((pid, "day", day,
                                 "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@" +
                                 day + "/v1/currencies/" + b.lower() + ".json"))
            else:
                # ECB publishes against EUR, so both series are pulled and the
                # cross rate is formed below.
                for cur, tag in ((q, "ecb_quote"), (b, "ecb_base")):
                    urls.append((tag, "csv", "",
                                 "https://data-api.ecb.europa.eu/service/data/EXR/D." + cur +
                                 ".EUR.SP00.A?startPeriod=" + first + "&endPeriod=" + last +
                                 "&format=csvdata"))

        def run() -> dict:
            ev = {}
            for tag, kind, day, url in urls:
                body = ""
                try:
                    body = self._body(gl.nondet.web.get(url))
                except Exception:
                    body = ""
                if kind == "range":
                    ev[tag] = self._p_frankfurter(body, days, b, q)
                elif kind == "day":
                    slot = ev.setdefault(tag, {})
                    for k, v in self._p_currencyapi(body, day, b, q).items():
                        slot[k] = v
                else:
                    ev[tag] = self._p_ecb(body, days)
            ev["ecb"] = self._pair(ev)

            rows = []
            for pid in d["providers"]:
                got = ev.get(pid, {})
                # A provider counts only when it covers the exact window asked
                # for: a 2-of-3 average is not the period average.
                rows.append({"provider": pid, "days": len(got),
                             "covered": len(got) == need,
                             "average": (sum(got.values()) // len(got)) if got else 0})

            good = [r for r in rows if r["covered"] and r["average"] > 0]
            out = {"rows": rows, "valid": len(good), "spread_bps": 0,
                   "average": 0, "payout": 0, "verdict": "UNVERIFIED"}
            if len(good) < 2:
                return out

            avgs = sorted([int(r["average"]) for r in good])
            mid = len(avgs) // 2
            average = avgs[mid] if len(avgs) % 2 else (avgs[mid - 1] + avgs[mid]) // 2
            spread_bps = ((avgs[-1] - avgs[0]) * 10000) // average if average else 0
            out["valid"] = len(good)
            out["spread_bps"] = spread_bps
            out["average"] = average
            out["payout"] = self._payout(notional, average, strike)
            out["verdict"] = ("DISAGREED" if spread_bps > int(d["tolerance_bps"])
                              else "VERIFIED")
            return out

        tol = self._agree_tol(int(d["tolerance_bps"]))

        def agrees(leader) -> bool:
            if not isinstance(leader, gl.vm.Return):
                return False
            return self._agree(leader.calldata, run(), tol, len(d["providers"]))

        res = gl.vm.run_nondet_unsafe(run, agrees)
        if not isinstance(res, dict):
            res = {}

        rows = res.get("rows")
        self.rates[deal_id] = json.dumps(rows if isinstance(rows, list) else [])
        d["valid_count"] = int(res.get("valid", 0))
        d["spread_bps"] = int(res.get("spread_bps", 0))
        d["average"] = int(res.get("average", 0))
        d["payout"] = int(res.get("payout", 0))
        verdict = str(res.get("verdict", ""))
        escrow = int(d["escrow"])

        if verdict != "VERIFIED":
            d["status"] = REFUNDED
            d["reason"] = ("providers did not agree" if verdict == "DISAGREED"
                           else "the period average could not be verified")
        else:
            payout = int(d["payout"])
            if payout <= 0:
                d["status"] = REFUNDED
                d["reason"] = "the payout came out at zero"
            elif payout > escrow:
                d["status"] = REFUNDED
                d["reason"] = "the settled payout exceeds the escrow"
            else:
                d["status"] = EXECUTED
                d["reason"] = "settled at the period average"
                d["payout"] = payout
                d["seller_paid"] = payout
                d["buyer_refund"] = escrow - payout
                self._save(d)
                self._transfer(Address(d["seller"]), payout)
                self._transfer(Address(d["buyer"]), escrow - payout)
                return EXECUTED

        d["payout"] = 0
        d["seller_paid"] = 0
        d["buyer_refund"] = escrow
        self._save(d)
        self._transfer(Address(d["buyer"]), escrow)
        return REFUNDED

    @gl.public.write
    def cancel(self, deal_id: str) -> str:
        d = self._load(deal_id)
        if str(d["status"]) != OPEN:
            raise gl.vm.UserError("Settlement is " + str(d["status"]))
        if self._addr_hex(gl.message.sender_address).lower() != str(d["buyer"]).lower():
            raise gl.vm.UserError("Only the buyer cancels")
        if self._now() >= int(d["settle_after"]):
            raise gl.vm.UserError("The settle window is open")
        d["status"] = CANCELLED
        d["reason"] = "cancelled by the buyer"
        d["buyer_refund"] = int(d["escrow"])
        self._save(d)
        self._transfer(Address(d["buyer"]), int(d["escrow"]))
        return CANCELLED

    @gl.public.write
    def expire(self, deal_id: str) -> str:
        d = self._load(deal_id)
        if str(d["status"]) != OPEN:
            raise gl.vm.UserError("Settlement is " + str(d["status"]))
        if self._now() <= int(d["deadline"]):
            raise gl.vm.UserError("The deadline has not passed")
        d["status"] = REFUNDED
        d["reason"] = "no settlement before the deadline"
        d["buyer_refund"] = int(d["escrow"])
        self._save(d)
        self._transfer(Address(d["buyer"]), int(d["escrow"]))
        return REFUNDED

    # ---------------- views ----------------

    @gl.public.view
    def get_settlement(self, deal_id: str) -> str:
        return json.dumps(self._load(deal_id))

    @gl.public.view
    def get_rates(self, deal_id: str) -> str:
        self._load(deal_id)
        raw = self.rates.get(str(deal_id), "")
        return raw if raw else "[]"

    @gl.public.view
    def get_deal_count(self) -> int:
        return int(self.deal_count)
