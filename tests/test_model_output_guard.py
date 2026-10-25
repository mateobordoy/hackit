"""Model output that used to be lost: a paragraph over the length limit is cut after its last full
sentence, the supplier's "8D report" no longer counts as a figure, and a JSON answer followed by a
second, unfinished block still yields its first object. Every figure is still refused."""

from hackit2 import model, settings

SENTENCE = "We value the partnership and want to find a fair agreement for both sides."


def test_short_paragraph_is_kept_whole():
    assert model.fit_paragraph(SENTENCE) == SENTENCE


def test_long_paragraph_is_cut_after_its_last_full_sentence():
    text = " ".join([SENTENCE] * 20)
    fitted = model.fit_paragraph(text)
    assert len(fitted) <= settings.PARAGRAPH_MAX_CHARS
    assert fitted.endswith(".") and text.startswith(fitted)


def test_cut_never_ends_on_a_title():
    text = "Thank you, Mr. Seidl, for the quote" + " and the clear explanation" * 40 + "."
    assert model.fit_paragraph(text) is None  # no full sentence fits, so nothing is sent


def test_eight_d_report_is_not_a_figure():
    assert not model.unsafe("Thank you for the 8D report and your careful work on quality.")
    assert not model.unsafe("Thank you for the 8D-report.")


def test_every_other_digit_is_still_refused():
    assert model.unsafe("Thank you for the 8D report on 3 complaints.")
    assert model.unsafe("We could commit to 12 months.")
    assert model.unsafe("As shown in idx:PPI-C241, prices fell.")


def test_first_json_object_wins_over_a_broken_second_block():
    assert model.first_json('{"trades": [], "tone": "warm"}\n{"trades": [') == {"trades": [], "tone": "warm"}
    assert model.first_json('Here is the brief: {"a": {"b": 2}} done') == {"a": {"b": 2}}


def test_writer_returns_the_cut_paragraph():
    text = " ".join([SENTENCE] * 20)
    paragraph = model.write_paragraph(lambda system, user: text, "prompt", timeout=5)
    assert paragraph and len(paragraph) <= settings.PARAGRAPH_MAX_CHARS and text.startswith(paragraph)
