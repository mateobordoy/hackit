"""Citable arguments. Each one is a single sentence that puts its figure next to its data IDs,
computed the same way the arena verifies it. Only arguments that push the price in our direction
are produced: a rising index or a should-cost above the quote would make the supplier's case.

Refs the data package cannot resolve (the sandbox points into a demo world) are cited without a
figure, so the arena checks them against their own values.

An argument is a dict:
  topic     its kind (should_cost, order_history, index, notice, rival, forecast, scorecard,
            hedged, open_book)
  refs      the data IDs it cites
  sentence  the text that goes into the e-mail
  weight    ranking strength, higher first
  rival     True for another bidder's quote or offer (counted once per supplier, not per case)
  bidder    for a live rival offer: the supplier that made it
"""

import re

INDEX_LABELS = {
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
HEDGED_PATTERN = r"covered at fixed prices|bought (their|our) [\w ,-]{1,60} forward|material covered until"
OPEN_BOOK_PATTERN = r"walk (us|you) through (their|our) calculation"
MIN_INDEX_FALL_PCT = -0.5  # a driver must have fallen at least this much to be worth citing


def month_minus(month, count):
    """'YYYY-MM' moved `count` months back."""
    year, mon = (int(x) for x in month.split("-"))
    mon -= count
    while mon <= 0:
        mon, year = mon + 12, year - 1
    return f"{year}-{mon:02d}"


def eur(amount):
    return f"EUR {amount:.2f}"


def pieces_per_price(row):
    """How many pieces a recorded price covers: some quotes and orders are priced per 100 or 1000."""
    try:
        return int(row.get("price_unit") or 1) or 1
    except (TypeError, ValueError):
        return 1


def argument(topic, refs, sentence, weight, **extra):
    return {"topic": topic, "refs": refs, "sentence": sentence, "weight": weight, **extra}


class EvidenceBank:
    """Arguments for one case. `pkg` is the data package or None; the case's own `facts` take
    precedence over the package."""

    def __init__(self, pkg, case):
        self.pkg, self.case = pkg, case
        self.case_facts = case.get("facts") or {}
        self.case_refs = list(case.get("refs") or [])

    # --- data access --------------------------------------------------------------------------

    def fact(self, ref):
        """The case's fact for `ref`, else the package's (Sunday release included), else None. Every
        lookup is keyed by part or supplier, so a known part keeps its drivers, forecasts and
        should-cost whatever refs the case lists."""
        if ref in self.case_facts:
            return self.case_facts[ref]
        return None if self.pkg is None else self.pkg.lookup(ref)

    def ids(self, prefix):
        found = set() if self.pkg is None else set(self.pkg.ids(prefix))
        found |= {k for k in self.case_facts if k.startswith(prefix)}
        found |= {r for r in self.case_refs if r.startswith(prefix)}
        return sorted(found)

    def query(self, sql, params=()):
        if self.pkg is None:
            return []
        try:
            return self.pkg.query(sql, params)
        except Exception:  # noqa: BLE001 - a package without SQLite still has its facts
            return []

    # --- single arguments ---------------------------------------------------------------------

    def should_cost(self, part, supplier_id, quote):
        chosen = None
        for ref in self.ids("cost:"):
            row = self.fact(ref)
            if not row or row.get("part") != part or not row.get("unit_cost"):
                continue
            if ref.endswith("-" + supplier_id.split(":")[-1]):
                chosen = (ref, row)
                break
            if chosen is None and "-" not in ref.split(":")[-1]:
                chosen = (ref, row)
        if not chosen:
            return None
        ref, row = chosen
        if quote and row["unit_cost"] >= quote * 0.98:
            return None  # at or above its price: that would argue its case
        return argument(
            "should_cost",
            [ref],
            f"Our bottom-up should-cost estimate for this part comes to {eur(row['unit_cost'])} per unit ({ref}).",
            5,
        )

    def order_history(self, part, supplier_id, quote):
        lines = self.query(
            "SELECT id FROM po_lines WHERE part_id = ? AND supplier_id = ? ORDER BY order_date DESC LIMIT 3",
            (part, supplier_id),
        )
        orders = [(line["id"], self.fact(line["id"])) for line in lines]
        orders = [(ref, row) for ref, row in orders if row and row.get("unit_price")]
        if len(orders) < 2:
            return None
        prices = [row["unit_price"] for _, row in orders]
        pieces = pieces_per_price(orders[0][1])
        if quote and min(prices) / pieces >= quote:
            return None  # figures stay as recorded (per 100 or 1000 where so priced): that is what is checked
        refs = [ref for ref, _ in orders]
        per = f" per {pieces} units" if pieces != 1 else ""
        if len(set(prices)) == 1:
            sentence = (
                f"Our last {len(refs)} orders for this part were placed at {eur(prices[0])}{per} ({', '.join(refs)})."
            )
        else:
            listed = " and ".join(eur(p) for p in prices)
            sentence = f"Our last orders for this part were placed at {listed}{per} ({', '.join(refs)})."
        return argument("order_history", refs, sentence, 4)

    def short_notice(self):
        letter = self.case.get("trigger_doc")
        letter_row = self.fact(letter) if letter else None
        if not letter_row or letter_row.get("days") is None:
            return None
        contract = next((r for r in self.case_refs if r.startswith("ctr:") and "#" not in r), None)
        contract_row = self.fact(contract) if contract else None
        if not contract_row or contract_row.get("days") is None or letter_row["days"] >= contract_row["days"]:
            return None
        sentence = (
            f"Your letter gives {letter_row['days']} days' notice ({letter}), while our agreement provides for "
            f"{contract_row['days']} days ({contract})."
        )
        return argument("notice", [letter, contract], sentence, 5)

    def yearly_change(self, series):
        """The series' change over the last twelve months as a share (what suppliers quote), or None."""
        refs = self.ids(f"idx:{series}@")
        if not refs:
            return None
        latest_month = refs[-1].split("@")[-1]
        start, end = self.fact(f"idx:{series}@{month_minus(latest_month, 12)}"), self.fact(refs[-1])
        if not start or not end or not start.get("value") or not end.get("value"):
            return None
        return end["value"] / start["value"] - 1

    def index_fall(self, series):
        """The steepest fall of a series over any window of up to twelve months that ends at its latest
        month (the window the arena checks). An index can be up over the year yet down over the last
        quarter: the fall is the argument suppliers accept, the rise would argue their case."""
        refs = self.ids(f"idx:{series}@")
        if not refs:
            return None
        end_month = refs[-1].split("@")[-1]
        end = self.fact(refs[-1])
        if not end or not end.get("value"):
            return None
        steepest = None
        for back in range(1, 13):
            start_month = month_minus(end_month, back)
            start = self.fact(f"idx:{series}@{start_month}")
            if not start or not start.get("value"):
                continue
            pct = (end["value"] / start["value"] - 1) * 100
            if steepest is None or pct < steepest[0]:
                steepest = (pct, start_month)
        if steepest is None or steepest[0] > MIN_INDEX_FALL_PCT:
            return None  # a rising driver supports the supplier
        pct, start_month = steepest
        label = INDEX_LABELS.get(series, f"The index {series}")
        first, last = f"idx:{series}@{start_month}", f"idx:{series}@{end_month}"
        return argument(
            "index",
            [first, last],
            f"{label} changed {pct:.1f}% from {start_month} to {end_month} ({first}, {last}).",
            4 + min(3.0, abs(pct) / 2),  # a falling driver outranks notes and orders
        )

    def cost_drivers(self, part):
        return [row["series_id"] for row in self.query("SELECT series_id FROM cost_drivers WHERE part_id = ?", (part,))]

    def rising_forecast(self, part):
        refs = self.ids(f"fc:{part.split(':')[-1]}@")
        row = self.fact(refs[-1]) if refs else None
        if not row or (row.get("change_pct") or 0) <= 0:
            return None
        return argument(
            "forecast", [refs[-1]], f"Our demand forecast for this part is up {row['change_pct']:.1f}% ({refs[-1]}).", 2
        )

    def rival_quotes(self, part, supplier_id, price):
        """Quotes listed in this case from other suppliers that undercut this supplier's price."""
        found = []
        for ref in self.case_refs:
            row = self.fact(ref) if ref.startswith("quote:") else None
            if not row or row.get("part") != part or row.get("supplier") == supplier_id:
                continue
            if not price or not row.get("unit_price"):
                continue
            pieces = pieces_per_price(row)
            if row["unit_price"] / pieces < price:
                per = f"per {pieces} units" if pieces != 1 else "per unit"
                found.append(
                    argument(
                        "rival",
                        [ref],
                        f"We hold another quote for this part at {eur(row['unit_price'])} {per} ({ref}).",
                        6,
                        rival=True,
                    )
                )
        return found

    def own_words(self, supplier_id):
        """The supplier's own statements in our records that weaken its case: material bought forward
        at fixed prices, an offer to open its calculation. Context for the reader, not a checked figure."""
        found = []
        # the ID index, not SQL: letters from the Sunday release only reach the index
        letters = [(ref, self.fact(ref) or {}) for ref in self.ids("doc:")]
        letters = sorted(
            (d for d in letters if d[1].get("supplier") == supplier_id), key=lambda d: str(d[1].get("date"))
        )
        for ref, row in reversed(letters):
            try:
                text = self.pkg.document(ref)
            except (FileNotFoundError, UnicodeDecodeError):
                text = ""
            if re.search(HEDGED_PATTERN, text, re.I):
                found.append(
                    argument(
                        "hedged",
                        [ref],
                        f"Your letter of {row.get('date')} ({ref}) states that your material is covered at fixed "
                        "prices, so recent index movements are not yet reaching your costs.",
                        5,
                    )
                )
            if re.search(OPEN_BOOK_PATTERN, text, re.I):
                found.append(
                    argument(
                        "open_book",
                        [ref],
                        f"In our call of {row.get('date')} ({ref}) you offered to take us through your "
                        "calculation; we would be glad to do that now.",
                        4,
                    )
                )
        seen, unique = set(), []
        for item in found:
            if item["topic"] not in seen:
                seen.add(item["topic"])
                unique.append(item)
        return unique

    def scorecard(self, supplier_id, quality_case):
        refs = self.ids(f"sc:{supplier_id.split(':')[-1]}@")
        if not refs:
            return None
        row = self.fact(refs[-1])
        if row is None:  # demo world: cite without a figure
            sentence = f"We have reviewed your delivery performance in our supplier scorecard ({refs[-1]})."
            return argument("scorecard", [refs[-1]], sentence, 2)
        quarter = row.get("quarter") or refs[-1].split("@")[-1]
        if quality_case:
            if (row.get("ppm") or 0) < 1000:
                return None
            sentence = f"Your deliveries ran at {row['ppm']} ppm in {quarter} ({refs[-1]})."
        else:
            if (row.get("otd_pct") or 100) >= 90:
                return None
            sentence = f"Your on-time delivery stood at {row['otd_pct']}% in {quarter} ({refs[-1]})."
        return argument("scorecard", [refs[-1]], sentence, 2)

    def unresolved_refs(self):
        """Case refs the package cannot resolve, cited without a figure."""
        templates = {
            "cost": ("should_cost", "Our bottom-up should-cost estimate for this part is on file ({refs}).", 5),
            "po": (
                "order_history",
                "We have compared your offer with our recent order prices for this part ({refs}).",
                4,
            ),
        }
        found = []
        for prefix, (topic, sentence, weight) in templates.items():
            refs = [r for r in self.case_refs if r.startswith(f"{prefix}:") and self.fact(r) is None]
            if refs:
                found.append(argument(topic, refs[:3], sentence.format(refs=", ".join(refs[:3])), weight))
        letter = self.case.get("trigger_doc")
        contract = next((r for r in self.case_refs if r.startswith("ctr:")), None)
        if letter and contract and self.fact(letter) is None and self.case.get("type") == "price_increase":
            sentence = f"Your letter ({letter}) gives less notice than our agreement provides for ({contract})."
            found.append(argument("notice", [letter, contract], sentence, 5))
        return found

    # --- per supplier -------------------------------------------------------------------------

    def for_supplier(self, supplier_id, quote):
        """Every argument for this supplier that pushes our way, strongest first, without duplicates."""
        case, found = self.case, []
        quality_case = case.get("type") == "quality" or case["buyer"].get("category_goal") == "quality_first"
        single_part = len(case.get("parts") or []) == 1  # a bundle's price is not one part's order price
        for part in case.get("parts") or []:
            history = self.order_history(part, supplier_id, quote) if single_part else None
            found += [a for a in (self.should_cost(part, supplier_id, quote), history) if a]
            found += [a for a in (self.index_fall(s) for s in self.cost_drivers(part)) if a]
            found += self.rival_quotes(part, supplier_id, quote)
            if forecast := self.rising_forecast(part):
                found.append(forecast)
        if notice := self.short_notice():
            found.append(notice)
        found += self.own_words(supplier_id)
        if card := self.scorecard(supplier_id, quality_case):
            found.append(card)
        found += self.unresolved_refs()
        seen, unique = set(), []
        for item in sorted(found, key=lambda a: -a["weight"]):
            if tuple(item["refs"]) not in seen:
                seen.add(tuple(item["refs"]))
                unique.append(item)
        return unique


def live_rival_offer(supplier_id, offer_id, price):
    """Another bidder's standing offer in this session, citable to the other bidders."""
    return argument(
        "rival",
        [offer_id],
        f"Another bidder offers {eur(price)} per unit for this part ({offer_id}).",
        7,
        rival=True,
        bidder=supplier_id,
    )
