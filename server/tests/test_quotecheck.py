import json

import pytest
from conftest import PAGES

from research_ledger import quotecheck as qc
from research_ledger.quotecheck import Refusal

FORMS = [PAGES]


def refusal(fn, *a):
    with pytest.raises(Refusal) as e:
        fn(*a)
    return e.value


def test_a_quote_on_its_page_with_its_numbers_passes():
    out = qc.check(FORMS, 2, "Site calibration 0.70", ["0.7"])
    assert out == {"ok": True, "page": 2, "numbers": ["0.7"]}


def test_matching_ignores_line_breaks_hyphens_ligatures_and_the_unicode_minus():
    assert qc.check(FORMS, 2, "With station embeddings the network reaches", [])["ok"]
    assert qc.check(FORMS, 2, "an improvement of -0.07 over site calibration", [-0.07])["ok"]
    assert qc.check([["the eﬃcient method wins here"]], 1, "the efficient method wins here")["ok"]
    assert qc.check([["a soft­hyphen does not count here"]], 1, "a softhyphen does not count here")["ok"]


def test_a_quote_on_another_page_is_refused_with_the_page_it_is_on():
    r = refusal(qc.check, FORMS, 1, "Site calibration 0.70", [])
    assert r.error == "the quote is not on page 1" and r.meaning == "it is on page 2"


def test_a_paraphrase_is_refused():
    r = refusal(qc.check, FORMS, 2, "site calibration scores a CRPS of 0.70", [])
    assert "on no page" in r.meaning and "never paraphrase" in r.fix


def test_a_number_not_written_in_the_quote_is_refused():
    assert qc.check(FORMS, 1, "We calibrate ensemble forecasts at 1,234 stations", [1234.0])["numbers"] == ["1234"]
    r = refusal(qc.check, FORMS, 2, "Site calibration 0.70", ["0.63"])
    assert (r.error, r.meaning) == ("0.63 is not written in the quote", "the quote writes 0.70")


def test_a_short_quote_and_a_missing_page_are_refused():
    assert "too short" in refusal(qc.check, FORMS, 2, "0.70", []).error
    assert refusal(qc.check, FORMS, 3, "Site calibration 0.70", []).meaning == "its pages are 1..2"


def test_numbers_are_read_as_written():
    assert qc.numbers("from 0.5-0.6 and 1,234 to −2") == {qc.Decimal(x) for x in ("0.5", "0.6", "1234", "-2")}


def test_the_script_prints_json_and_exits_by_the_verdict(paper, capsys):
    assert qc.main([str(paper), "--page", "2", "--quote", "Site calibration 0.70", "--number", "0.7"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert qc.main([str(paper), "--page", "1", "--quote", "Site calibration 0.70"]) == 1
    assert json.loads(capsys.readouterr().out)["meaning"] == "it is on page 2"
