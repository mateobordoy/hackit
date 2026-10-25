"""The reader agent's guards, offline: a label stands only on a quote that is in the text, from the
closed lists; the summary carries no figures; off without a model."""

from buyer import reader

MAIL = "Hello,\nWe see your figures. Faster payment would help us more than anything.\nLogistics: We are well loaded."


def test_labels_need_a_quote_from_the_text():
    raw = {
        "signals": [
            {"label": "cash_need", "quote": "Faster payment would help us more than anything."},
            {"label": "capacity_full", "quote": "logistics:  we are WELL loaded"},
            {"label": "hedged", "quote": "we bought our steel forward"},  # not in the text
            {"label": "made_up", "quote": "We see your figures."},  # not a label
        ],
        "asks": [{"label": "payment", "quote": "Faster payment would help us"}],
        "stance": "holding",
        "summary": "Short of cash, 3 weeks of orders.",
    }
    out = reader.validate(raw, MAIL)
    assert out["signals"] == ["cash_need", "capacity_full"]
    assert out["asks"] == ["payment"]
    assert out["stance"] == "holding"
    assert not any(ch.isdigit() for ch in out["summary"])


def test_short_quotes_and_unknown_stance_are_dropped():
    raw = {"signals": [{"label": "cash_need", "quote": "We"}], "asks": [], "stance": "angry", "summary": ""}
    out = reader.validate(raw, MAIL)
    assert out["signals"] == [] and out["stance"] == "unclear"


def test_off_without_a_model(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert reader.submit("supplier e-mail", MAIL) is None
    assert reader.result(None, 5) is None
