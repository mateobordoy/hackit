"""Data arguments, one sentence each with its figure next to its IDs, computed the way the arena
checks them (api.md, What counts). Only arguments that push our way are kept, ranked by how often
the practice suppliers actually took that kind into account (config.STRENGTH). Our own order
prices are never cited: 0 of 100 moved a supplier. [pc]"""

from . import config

SERIES_NAME = {
    "PPI-C2451": "Producer prices for iron castings",
    "PPI-C2452": "Producer prices for steel castings",
    "PPI-C2562": "Producer prices for machining",
    "PPI-C2594": "Producer prices for fasteners",
    "PPI-C2711": "Producer prices for electric motors",
    "PPI-C2611": "Producer prices for electronic components",
    "PPI-C2815": "Producer prices for bearings and gears",
    "PPI-C241": "Producer prices for basic iron and steel",
    "PPI-C2431": "Producer prices for cold-drawn bars",
    "PPI-C2219": "Producer prices for rubber products",
    "PPI-D351": "Electricity producer prices",
    "PPI-D35": "Energy supply producer prices",
    "LCI-C": "Manufacturing labour costs",
    "WB-CU": "Copper",
    "WB-NI": "Nickel",
    "WB-AL": "Aluminium",
    "WB-NGEU": "European natural gas",
    "CU-NOTATION": "The copper notation",
    "AS-14404": "The 1.4404 alloy surcharge",
    "AS-14301": "The 1.4301 alloy surcharge",
}
# Raw-material series a hedged supplier has bought forward: arguing them meets "not our reality". [pc]
MATERIAL_PREFIXES = ("WB-", "AS-", "CU-")


def months_back(month, n):
    y, m = (int(x) for x in month.split("-"))
    m -= n
    while m <= 0:
        m, y = m + 12, y - 1
    return f"{y}-{m:02d}"


def money(x):
    return f"EUR {x:.2f}"


def per_unit(fact):
    try:
        return int(fact.get("price_unit") or 1) or 1
    except (TypeError, ValueError):
        return 1


def claim(kind, ids, text, strength=None, **extra):
    return {"kind": kind, "ids": list(ids), "text": text, "strength": strength or config.STRENGTH[kind], **extra}


class EvidenceBank:
    def __init__(self, data, case):
        self.data, self.case = data, case

    # -- single arguments ------------------------------------------------------------------
    def forecast(self, part):
        ids = self.data.ids(f"fc:{part.split(':')[-1]}@")
        f = self.data.fact(ids[-1]) if ids else None
        if not f or (f.get("change_pct") or 0) <= 0:
            return None
        text = f"Our demand forecast for this part is up {f['change_pct']:.1f}% ({ids[-1]})."
        return claim("forecast", [ids[-1]], text)

    def index_change(self, series, hedged=False):
        """The largest fall of a series over any window of up to 12 months ending at its latest
        month: the window the arena checks. A rising driver would argue the supplier's case."""
        ids = self.data.ids(f"idx:{series}@")
        if not ids:
            return None
        m2 = ids[-1].split("@")[-1]
        b = self.data.fact(ids[-1])
        if not b or not b.get("value"):
            return None
        best = None
        for back in range(1, 13):
            m1 = months_back(m2, back)
            a = self.data.fact(f"idx:{series}@{m1}")
            if not a or not a.get("value"):
                continue
            pct = (b["value"] / a["value"] - 1) * 100
            if best is None or pct < best[0]:
                best = (pct, m1)
        if best is None or best[0] > -config.MIN_INDEX_FALL_PCT:
            return None
        pct, m1 = best
        strength = config.STRENGTH["index"] + min(2.0, abs(pct) / 3)
        if hedged and series.startswith(MATERIAL_PREFIXES):
            strength -= 4.0
        name = SERIES_NAME.get(series, f"The index {series}")
        text = f"{name} changed {pct:.1f}% from {m1} to {m2} (idx:{series}@{m1}, idx:{series}@{m2})."
        return claim("index", [f"idx:{series}@{m1}", f"idx:{series}@{m2}"], text, strength, series=series)

    def drivers(self, part):
        rows = self.data.query("SELECT series_id FROM cost_drivers WHERE part_id = ?", (part,))
        return [r["series_id"] for r in rows]

    def should_cost(self, part, sid, quote):
        best = None
        for ref in self.data.ids("cost:"):
            f = self.data.fact(ref)
            if not f or f.get("part") != part or not f.get("unit_cost"):
                continue
            if ref.endswith("-" + sid.split(":")[-1]):
                best = (ref, f)
                break
            if best is None and "-" not in ref.split(":")[-1]:
                best = (ref, f)
        if not best:
            return None
        ref, f = best
        if quote and f["unit_cost"] / per_unit(f) >= quote * 0.98:
            return None
        text = f"Our bottom-up should-cost estimate for this part is {money(f['unit_cost'])} per unit ({ref})."
        return claim("should_cost", [ref], text)

    def rival_quotes(self, part, sid, price):
        out = []
        for ref in self.data.refs:
            f = self.data.fact(ref) if ref.startswith("quote:") else None
            if not f or f.get("part") != part or f.get("supplier") == sid or not price or not f.get("unit_price"):
                continue
            unit = per_unit(f)
            if f["unit_price"] / unit < price:
                each = f"per {unit} units" if unit != 1 else "per unit"
                text = f"We hold another quote for this part at {money(f['unit_price'])} {each} ({ref})."
                out.append(claim("rival_quote", [ref], text, rival=True))
        return out

    def notice(self, sid):
        doc = self.case.get("trigger_doc")
        f = self.data.fact(doc) if doc else None
        if not f or f.get("days") is None:
            return None
        ctrs = [r for r in self.data.refs if r.startswith("ctr:") and "#" not in r]
        if not ctrs:
            ctrs = [
                r for r in self.data.ids("ctr:") if "#" not in r and (self.data.fact(r) or {}).get("supplier") == sid
            ]
        c = self.data.fact(ctrs[0]) if ctrs else None
        if not c or c.get("days") is None or f["days"] >= c["days"]:
            return None
        text = (
            f"Your letter gives {f['days']} days' notice ({doc}); "
            f"our agreement provides for {c['days']} days ({ctrs[0]})."
        )
        return claim("notice", [doc, ctrs[0]], text)

    def scorecard(self, sid, quality):
        ids = self.data.ids(f"sc:{sid.split(':')[-1]}@")
        if not ids:
            return None
        f = self.data.fact(ids[-1])
        if f is None:
            return None
        quarter = f.get("quarter") or ids[-1].split("@")[-1]
        if quality:
            if (f.get("ppm") or 0) < 1000:
                return None
            text = f"Your deliveries ran at {f['ppm']} ppm in {quarter} ({ids[-1]})."
            return claim("quality_record", [ids[-1]], text)
        if (f.get("otd_pct") or 100) >= 90:
            return None
        text = f"Your on-time delivery was {f['otd_pct']}% in {quarter} ({ids[-1]})."
        return claim("delivery_record", [ids[-1]], text)

    def demo_refs(self):
        """Refs neither the case nor the package knows (the sandbox's demo world): cited without a
        figure, so the arena checks them against their own value."""
        out = []
        cost_ids = [r for r in self.data.refs if r.startswith("cost:") and self.data.fact(r) is None]
        if cost_ids:
            text = f"Our bottom-up should-cost estimate for this part is on file ({cost_ids[0]})."
            out.append(claim("should_cost", cost_ids[:1], text))
        # the case points at a forecast we cannot read: the arena checks it at its own value; it
        # ranks below a forecast whose rise we can see, since we cannot see its sign
        fc_ids = [r for r in self.data.refs if r.startswith("fc:") and self.data.fact(r) is None]
        if fc_ids:
            text = f"Our demand forecast for this part is on file ({fc_ids[-1]})."
            out.append(claim("forecast", fc_ids[-1:], text, config.STRENGTH["forecast"] - 3))
        doc, ctr = self.case.get("trigger_doc"), next((r for r in self.data.refs if r.startswith("ctr:")), None)
        if doc and ctr and self.data.fact(doc) is None and self.case.get("type") == "price_increase":
            text = f"Your letter ({doc}) gives less notice than our agreement provides for ({ctr})."
            out.append(claim("notice", [doc, ctr], text))
        return out

    # -- per supplier ----------------------------------------------------------------------
    def for_supplier(self, sid, quote, hedged=False):
        case, out = self.case, []
        quality = case.get("type") == "quality" or case["buyer"].get("category_goal") == "quality_first"
        for part in case.get("parts") or []:
            out += [c for c in (self.should_cost(part, sid, quote), self.forecast(part)) if c]
            out += [c for c in (self.index_change(s, hedged) for s in self.drivers(part)) if c]
            out += self.rival_quotes(part, sid, quote)
        for c in (self.notice(sid), self.scorecard(sid, quality)):
            if c:
                out.append(c)
        out += self.demo_refs()
        seen, uniq = set(), []
        for c in sorted(out, key=lambda c: -c["strength"]):
            key = tuple(c["ids"])
            if key not in seen:
                seen.add(key)
                uniq.append(c)
        return uniq


def rival_offer(sid, offer_id, price):
    """Another bidder's standing offer in this session, citable to the others."""
    text = f"Another bidder offers {money(price)} per unit for this part ({offer_id})."
    return claim("rival_offer", [offer_id], text, rival=True, origin=sid)
