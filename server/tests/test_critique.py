import pytest

from research_ledger import critique as cq
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal

LOG = "solver iterations: 2 to 5\nCPU runs: 416\nblow-ups: 6 of 40 runs\n"
DRAFT = "The solver needs 2 to 3 iterations. We ran 384 CPU runs; about 25% blew up.\n"


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


@pytest.fixture
def work(tmp_path, led):
    """A draft, a log it should rest on, and a critique of the draft with the reviewer's own citation."""
    (tmp_path / "run.log").write_text(LOG)
    draft = tmp_path / "issue.md"
    draft.write_text(DRAFT)
    log = led.add_source(str(tmp_path / "run.log"))
    v1 = cq.record_draft(led, str(draft))
    seen = led.cite(log["id"], 1, "CPU runs: 416\nblow-ups: 6 of 40 runs", ["416"])
    c = cq.critique(led, v1["id"], [
        {"text": "384 CPU runs is wrong; the log says 416", "where": "line 1", "blocking": True,
         "evidence": [seen["id"]]},
        {"text": "the damping ratio is a port bug", "blocking": True},
        {"text": "say which GPU", "blocking": False}], persona="adversary")
    return {"draft": draft, "log": log, "v1": v1, "critique": c}


def test_a_draft_is_recorded_with_the_numbers_no_citation_writes(led, tmp_path):
    (tmp_path / "d.md").write_text("Mean skill 0.71 over 12 stations.")
    d = cq.record_draft(led, str(tmp_path / "d.md"))
    assert d["body"]["version"] == 1 and d["untraced_numbers"] == ["0.71", "12"]
    assert cq.record_draft(led, str(tmp_path / "d.md"))["already"]


def test_a_critique_records_its_findings_and_marks_the_unsupported(work):
    c = work["critique"]
    assert [f["id"] for f in c["body"]["findings"]] == ["F1", "F2", "F3"]
    assert (c["blocking"], c["unsupported"]) == (2, 2) and c["body"]["persona"] == "adversary"


def test_evidence_must_be_a_citation_or_run(led, work):
    with pytest.raises(Refusal, match="is a source, not evidence"):
        cq.critique(led, work["v1"]["id"], [{"text": "x is wrong", "evidence": [work["log"]["id"]]}])


def test_a_blocking_answer_needs_evidence_recorded_after_the_critique(led, work):
    c = work["critique"]["id"]
    reviewers = work["critique"]["body"]["findings"][0]["evidence"]
    with pytest.raises(Refusal, match="is blocking, and is answered with your own evidence"):
        cq.answer(led, c, "F1", "upheld", "the reviewer says so")
    with pytest.raises(Refusal, match="was recorded before the critique"):
        cq.answer(led, c, "F1", "upheld", evidence=reviewers)
    mine = led.cite(work["log"]["id"], 1, "CPU runs: 416\nblow-ups: 6 of 40 runs", ["416"])
    assert cq.answer(led, c, "F1", "upheld", "the log says 416", [mine["id"]])["body"]["verdict"] == "upheld"
    with pytest.raises(Refusal, match="already answered"):
        cq.answer(led, c, "F1", "dismissed", "changed my mind", [mine["id"]])


def test_a_dismissal_needs_a_reason_and_a_minor_finding_can_rest_on_one(led, work):
    c = work["critique"]["id"]
    with pytest.raises(Refusal, match="a dismissal says why"):
        cq.answer(led, c, "F3", "dismissed")
    assert cq.answer(led, c, "F3", "upheld", "add the GPU model")


def test_a_revision_waits_for_every_blocking_answer(led, work, tmp_path):
    c, v1 = work["critique"]["id"], work["v1"]["id"]
    work["draft"].write_text(DRAFT.replace("384", "416"))
    with pytest.raises(Refusal, match=r"#F1 is blocking and unanswered \(1 more\)"):
        cq.record_draft(led, str(work["draft"]), supersedes=v1)
    mine = led.cite(work["log"]["id"], 1, "CPU runs: 416\nblow-ups: 6 of 40 runs", ["416"])
    cq.answer(led, c, "F1", "upheld", "the log says 416", [mine["id"]])
    (tmp_path / "scene.xml").write_text('<geom solref="0.001 2"/> written by the scene builder itself\n')
    scene = led.add_source(str(tmp_path / "scene.xml"))
    shown = led.cite(scene["id"], 1, '<geom solref="0.001 2"/> written by the scene builder')
    cq.answer(led, c, "F2", "dismissed", "the original applies 2 itself", [shown["id"]])
    v2 = cq.record_draft(led, str(work["draft"]), supersedes=v1)
    assert v2["body"]["version"] == 2 and v2["body"]["answered"] == [f"{c}#F1", f"{c}#F2"]
    assert cq.record_draft(led, str(work["draft"]), supersedes=v1)["already"]
    work["draft"].write_text(DRAFT.replace("384", "416") + "Tested on one GPU.\n")
    with pytest.raises(Refusal, match="already superseded"):
        cq.record_draft(led, str(work["draft"]), supersedes=v1)


def test_verify_reports_an_upheld_finding_left_standing_and_lists_open_ones(led, work):
    c, v1 = work["critique"]["id"], work["v1"]["id"]
    v = led.verify()
    assert v["ok"] and v["open_findings"] == [f"{c}#F1", f"{c}#F2"] and v["draft"] == 1 and v["critique"] == 1
    mine = led.cite(work["log"]["id"], 1, "CPU runs: 416\nblow-ups: 6 of 40 runs", ["416"])
    cq.answer(led, c, "F1", "upheld", "the log says 416", [mine["id"]])
    v = led.verify()
    assert not v["ok"] and v["problems"] == [f"{c}#F1 was upheld, and {v1} still stands (supersede it with a revision)"]
    assert v["personas"] == {"adversary": {"upheld": 1, "dismissed": 0, "open": 2}}


def test_a_claim_resting_on_a_refuted_citation_must_be_superseded(led, work):
    log = work["log"]["id"]
    wrong = led.cite(log, 1, "blow-ups: 6 of 40 runs", ["6", "40"])
    old = led.claim("6 of 40 runs blew up.", [wrong["id"]])
    c = cq.critique(led, wrong["id"], [{"text": "those runs used other settings", "blocking": True}], "reviewer")
    check = led.cite(log, 1, "solver iterations: 2 to 5\nCPU runs: 416")
    cq.answer(led, c["id"], "F1", "upheld", "the log mixes settings", [check["id"]])
    assert any(f"{old['id']} still rests on it" in p for p in led.verify()["problems"])
    led.claim("The CPU made 416 runs.", [check["id"]], supersedes=old["id"])
    assert led.verify()["ok"]


def test_a_claim_revision_waits_for_blocking_findings_on_it(led, work):
    c = led.cite(work["log"]["id"], 1, "CPU runs: 416\nblow-ups: 6 of 40 runs", ["416"])
    old = led.claim("The CPU made 416 runs.", [c["id"]])
    cq.critique(led, old["id"], [{"text": "416 counts warm-up runs", "blocking": True}])
    with pytest.raises(Refusal, match="is blocking and unanswered"):
        led.claim("The CPU made 416 runs, warm-up included.", [c["id"]], supersedes=old["id"])


def test_verify_finds_a_changed_draft_text(led, work):
    blob = led.blobs / work["v1"]["body"]["text"]
    blob.write_text(DRAFT.replace("384", "416"))
    assert any("its text was changed" in p for p in led.verify()["problems"])


def test_listing_shows_status_and_filters_open(led, work):
    c = work["critique"]["id"]
    cq.answer(led, c, "F3", "upheld", "add the GPU model")
    rows = cq.listing(led, work["v1"]["id"])
    assert [r["status"] for r in rows] == ["open", "open", "upheld"]
    assert [r["ref"] for r in cq.listing(led, open_only=True)] == [f"{c}#F1", f"{c}#F2"]


def test_a_draft_is_quoted_by_its_id_and_its_file_is_not_a_source(led, work):
    v1 = work["v1"]["id"]
    c = led.cite(v1, 1, "We ran 384 CPU runs; about 25% blew up.", ["384"])
    assert c["body"]["source"] == v1
    with pytest.raises(Refusal, match="is recorded as the draft"):
        led.add_source(str(work["draft"]))
    work["draft"].write_text(DRAFT.replace("384", "416"))
    assert led.verify()["ok"]


def test_an_unchanged_revision_is_refused_and_draft_quotes_trace_nothing(led, work):
    c, v1 = work["critique"]["id"], work["v1"]["id"]
    led.cite(v1, 1, "The solver needs 2 to 3 iterations.")
    mine = led.cite(work["log"]["id"], 1, "CPU runs: 416\nblow-ups: 6 of 40 runs", ["416"])
    cq.answer(led, c, "F1", "upheld", "the log says 416", [mine["id"]])
    cq.answer(led, c, "F2", "upheld", "checked", [mine["id"]])
    with pytest.raises(Refusal, match="is unchanged since"):
        cq.record_draft(led, str(work["draft"]), supersedes=v1)
    work["draft"].write_text("The solver needs 2 to 3 iterations; the CPU made 416 runs.\n")
    assert cq.record_draft(led, str(work["draft"]), supersedes=v1)["untraced_numbers"] == ["2", "3"]


def test_a_draft_lists_its_wording_to_check_and_marks_quoted_uses(led, tmp_path):
    (tmp_path / "w.md").write_text('A novel loss that proves the point.\n'
                                   '> the authors call it "novel"\n'
                                   '```\nproves nothing: code\n```\n'
                                   'They claim a "novel" result; we improved on it.\n')
    d = cq.record_draft(led, str(tmp_path / "w.md"))
    hits = [(w["rule"], w["line"], w["word"], w["quoted"]) for w in d["wording"]]
    assert hits == [("novelty", 1, "novel", False), ("proof", 1, "proves", False),
                    ("novelty", 2, "novel", True), ("proof", 4, "proves", True), ("novelty", 6, "novel", True)]
    assert d["wording"][0]["sentence"] == "A novel loss that proves the point." and d["wording"][0]["need"]
    assert cq.record_draft(led, str(tmp_path / "w.md"))["wording"] == d["wording"]
    (tmp_path / "ok.md").write_text("The result supports a gain of 0.2, interval 0.1 to 0.3; approved.\n")
    assert cq.record_draft(led, str(tmp_path / "ok.md"))["wording"] == []


def test_a_claim_answers_with_its_wording_but_is_recorded(led, work):
    c = led.cite(work["log"]["id"], 1, "CPU runs: 416\nblow-ups: 6 of 40 runs", ["416"])
    k = led.claim("416 CPU runs prove the solver is stable.", [c["id"]])
    assert k["id"].startswith("claim:") and [w["word"] for w in k["wording"]] == ["prove"]
    assert "wording" not in led.claim("The CPU made 416 runs.", [c["id"]]) and led.verify()["ok"]
