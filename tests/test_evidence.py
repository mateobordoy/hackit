"""The evidence bank on cases whose refs the data package cannot resolve (the sandbox's demo world,
or a hidden case with fresh IDs): a known part keeps its package-backed arguments, unknown refs are
cited without a figure, and a Sunday-release letter becomes a citable argument."""

from pathlib import Path

import pytest
from arena_kit.data import DataPackage
from arena_kit.sim import load_cases
from conftest import SundayReleasePackage

from hackit2.evidence import EvidenceBank

PACKAGE_DIR = Path(__file__).parent.parent / "data" / "package"
FAR_ABOVE_ANY_PRICE = 1e9  # a quote above every recorded figure: every argument points our way


@pytest.fixture(scope="module")
def data_package():
    if not PACKAGE_DIR.exists():
        pytest.skip("no data package (uv run arena data)")
    return DataPackage(str(PACKAGE_DIR))


def first_supplier(case):
    supplier = case["suppliers"][0]
    return supplier["supplier_id"] if isinstance(supplier, dict) else supplier


def argument_topics(bank, case):
    return {arg["topic"] for arg in bank.for_supplier(first_supplier(case), FAR_ABOVE_ANY_PRICE)}


def test_foreign_refs_keep_the_package_arguments_of_a_known_part(data_package):
    case = next(c for c in load_cases().values() if c.get("refs"))
    with_known_refs = argument_topics(EvidenceBank(data_package, case), case)
    foreign_case = {**case, "refs": ["sc:NEW-1@2026Q3", "po:4500009999", "cost:part:NEW"]}
    with_foreign_refs = argument_topics(EvidenceBank(data_package, foreign_case), foreign_case)
    package_backed = {"index", "forecast", "should_cost", "order_history"} & with_known_refs
    assert package_backed, with_known_refs
    assert package_backed <= with_foreign_refs, (with_known_refs, with_foreign_refs)


def test_unresolvable_refs_are_cited_without_figures(data_package):
    case = next(iter(load_cases().values()))
    foreign_case = {**case, "refs": ["cost:part:NEW", "po:4500009999", "po:4500009998"]}
    arguments = EvidenceBank(data_package, foreign_case).for_supplier(first_supplier(case), FAR_ABOVE_ANY_PRICE)
    by_refs = {tuple(arg["refs"]): arg for arg in arguments}
    assert ("cost:part:NEW",) in by_refs
    assert ("po:4500009999", "po:4500009998") in by_refs
    assert "EUR" not in by_refs[("cost:part:NEW",)]["sentence"]


def test_sunday_release_letter_becomes_a_hedging_argument():
    class HedgedLetters(SundayReleasePackage):
        def document(self, ref):
            return "we bought our bright and cold-drawn bar requirement forward at fixed prices"

    found = EvidenceBank(HedgedLetters(), {}).own_words("sup:S")
    # newest letter first, and one argument per topic
    assert [(arg["topic"], arg["refs"]) for arg in found] == [("hedged", ["doc:new"])]
