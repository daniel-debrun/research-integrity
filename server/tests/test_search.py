"""Search finds passages to quote; a changed source's citations are followed into the new text."""

import pytest

from research_ledger import search as sr
from research_ledger.ledger import Ledger


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


def test_search_ranks_the_passage_that_answers(led, paper, tmp_path):
    led.add_source(str(paper))
    other = tmp_path / "other.txt"
    other.write_text("A survey of station networks and their maintenance budgets.\n")
    led.add_source(str(other))
    out = sr.search(led, "mean CRPS of the network with station embeddings")
    top = out["passages"][0]
    assert top["page"] == 2 and "0.63" in top["numbers"] and "candidates only" in out["note"]
    assert len(led.entries()) == 2  # searching records nothing


def test_a_changed_source_says_where_each_quote_went(led, paper):
    src = led.add_source(str(paper))
    same = led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    moved = led.cite(src["id"], 1, "We calibrate ensemble forecasts at 1,234 stations", ["1,234"])
    gone = led.cite(src["id"], 2, "the network reaches a mean CRPS of 0.63", ["0.63"])
    text = paper.read_text().replace("the network reaches a mean CRPS of 0.63", "the network reaches 0.66")
    pages = text.split("\f")
    pages[0], pages[1] = pages[0].replace("We calibrate ensemble forecasts at 1,234 stations", ""), \
        pages[1] + "\nWe calibrate ensemble forecasts at 1,234 stations over two years.\n"
    paper.write_text("\f".join(pages))
    v = led.verify()
    c = v["changed_sources"]
    assert c["citations"] == 3 and c["same"] == 1
    assert [m["citation"] for m in c["moved"]] == [moved["id"]] and c["moved"][0]["pages"] == [2]
    assert [m["citation"] for m in c["gone"]] == [gone["id"]]
    assert same["id"] not in {m["citation"] for m in c["moved"] + c["gone"]}
