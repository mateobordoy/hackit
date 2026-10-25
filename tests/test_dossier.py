"""The supplier dossier: the committed file is complete and well formed, the agent reads only valid
labels from it, and a missing or broken file leaves the agent as without it."""

import json

import pytest

from buyer import dossier
from buyer.supplier import SIGNALS


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(dossier, "_DATA", None)
    yield
    monkeypatch.setattr(dossier, "_DATA", None)


def test_the_committed_dossier_covers_the_46_with_well_formed_fields(fresh):
    doc = json.loads(dossier.PATH.read_text(encoding="utf-8"))
    sups = doc["suppliers"]
    assert len(sups) == 46
    for sid, e in sups.items():
        assert sid.startswith("sup:")
        for key in (
            "name",
            "company",
            "terms",
            "our_payments",
            "quality",
            "dependency",
            "costing",
            "reading",
            "review",
        ):
            assert key in e, (sid, key)
        assert e["reading"]["summary"] and not any(ch.isdigit() for ch in e["reading"]["summary"])
        for s in e["reading"]["signals"]:
            assert s["label"] in SIGNALS and s["quote"] and s["source"], (sid, s)


def test_reading_keeps_only_known_labels(fresh, tmp_path, monkeypatch):
    path = tmp_path / "dossier.json"
    path.write_text(
        json.dumps(
            {
                "suppliers": {
                    "sup:1": {
                        "reading": {
                            "signals": [{"label": "cash_need", "quote": "the bank is watching"}, {"label": "rich"}],
                            "summary": "Short of cash.",
                            "sources": ["doc:1"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dossier, "PATH", path)
    r = dossier.reading("sup:1")
    assert r["signals"] == ["cash_need"] and r["sources"] == ["doc:1"] and r["stance"] == "unclear"
    assert "cash_need" in dossier.context("sup:1")
    assert dossier.reading("sup:2") is None and dossier.context("sup:2") == ""


def test_a_missing_or_broken_file_is_no_dossier(fresh, tmp_path, monkeypatch):
    monkeypatch.setattr(dossier, "PATH", tmp_path / "missing.json")
    assert dossier.reading("sup:70140") is None
    monkeypatch.setattr(dossier, "_DATA", None)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(dossier, "PATH", broken)
    assert dossier.reading("sup:70140") is None
