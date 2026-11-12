"""Source types, freshness, DOI notices, claim conflicts, and a draft's lines against the claims."""

from datetime import datetime, timedelta, timezone

import pytest

from research_ledger import meta as mt
from research_ledger.critique import record_draft
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


def test_a_type_is_an_annotation_and_old_sources_read_unknown(led, paper):
    src = led.add_source(str(paper))
    assert mt.status(led.entries(), src)["type"] == "unknown"
    with pytest.raises(Refusal, match="not a source type"):
        mt.annotate(led, src["id"], "blog")
    mt.annotate(led, src["id"], "vendor")
    st = mt.status(led.entries(), src, now=datetime.now(timezone.utc) + timedelta(days=91))
    assert st == {"type": "vendor", "stale": True, "notices": []}
    assert not mt.status(led.entries(), src)["stale"] and led.verify()["ok"]


def test_a_retraction_notice_is_listed_against_the_claims_resting_on_it(led, paper):
    src = led.add_source(str(paper))
    c = led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    k = led.claim("Site calibration reaches 0.70.", [c["id"]])
    fake = lambda doi: [{"type": "retraction", "doi": "10.1000/notice.1", "date": "2026-1-2"}]
    with pytest.raises(Refusal, match="not a DOI"):
        mt.annotate(led, src["id"], doi="nature12373", fetch=fake)
    mt.annotate(led, src["id"], "paper", doi="https://doi.org/10.1000/paper.1", fetch=fake)
    w = led.verify()["source_notices"]
    assert w[0]["claim"] == k["id"] and "retraction notice" in w[0]["why"][0]


def test_claims_on_one_subject_with_different_numbers_conflict(led, paper):
    src = led.add_source(str(paper))
    a = led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    b = led.cite(src["id"], 2, "the network reaches a mean CRPS of 0.63", ["0.63"])
    led.claim("The best mean CRPS is 0.70.", [a["id"]], subject="crps.best")
    k = led.claim("The best mean CRPS is 0.63.", [b["id"]], subject="crps.best")
    assert k["conflicts"][0]["subject"] == "crps.best"
    assert led.verify()["conflicts"][0]["numbers"][k["id"]] == ["0.63"]


def test_a_draft_lists_lines_whose_numbers_no_claim_writes(led, paper, tmp_path):
    src = led.add_source(str(paper))
    c = led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    led.claim("Site calibration reaches 0.70.", [c["id"]])
    d = tmp_path / "report.md"
    d.write_text("# Results\nSite calibration reaches 0.70 (Table 4).\nThe network reaches 0.58.\nNo numbers here.\n")
    out = record_draft(led, str(d))
    assert out["grounded_lines"] == 1
    assert out["unclaimed_lines"] == [{"line": 3, "text": "The network reaches 0.58.", "numbers": ["0.58"]}]
