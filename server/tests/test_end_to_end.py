"""One session through the MCP interface, in a fresh folder: sources, search, cite, claim, facts, decisions, a
report, a draft, verify, the digest."""

import json

from research_ledger.server import build
from test_server import run


def test_a_session_end_to_end(tmp_path, paper):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "plugin.json").write_text(json.dumps({"name": "demo", "version": "1.2.3"}))
    calls = [
        ("add_source", {"path_or_url": str(paper), "source_type": "paper"}),
        ("search_sources", {"query": "network mean CRPS station embeddings"}),
    ]
    _, out = run(build(proj / ".ledger"), calls)
    src, found = out[0]["id"], out[1]["passages"][0]
    assert out[0]["meta"]["source_type"] == "paper" and found["page"] == 2
    calls = [
        ("cite", {"source": src, "page": 2, "quote": "the network reaches a mean CRPS of 0.63", "numbers": ["0.63"]}),
        ("record_fact", {"probe": "field", "target": str(proj / "plugin.json"), "key": "version", "expect": "1.2.3"}),
        ("decide", {"subject": "release.version", "question": "Which version ships?", "choice": "1.2.3",
                    "reason": "the manifest says so", "new_subject": True}),
        ("check_report", {"path_or_text": f"Edited {proj}/plugin.json and {proj}/missing.py.", "cwd": str(proj)}),
    ]
    _, out = run(build(proj / ".ledger"), calls)
    cite, fact, dec, rep = out
    assert fact["statement"].endswith('version = "1.2.3"') and dec["ok"]
    assert rep["counts"]["confirmed"] == 1 and rep["counts"]["missing"] == 1
    draft = proj / "report.md"
    draft.write_text("The network reaches 0.63.\nIt also reaches 0.5 elsewhere.\n")
    (proj / "plugin.json").write_text(json.dumps({"name": "demo", "version": "1.2.4"}))
    calls = [
        ("claim", {"text": "The network reaches 0.63.", "citations": [cite["id"]]}),
        ("record_draft", {"path": str(draft)}),
        ("recall", {"query": "version plugin.json"}),
        ("verify", {}),
        ("write_digest", {"path": str(proj / "AGENTS.md")}),
    ]
    _, out = run(build(proj / ".ledger"), calls)
    claim, rec, recall, verify, digest = out
    assert claim["ok"] and rec["unclaimed_lines"][0]["numbers"] == ["0.5"]
    assert {h["state"] for h in recall["hits"] if h["kind"] == "fact"} == {"stale"}
    assert verify["ok"] and verify["stale_facts"] and verify["fact"] == 1 and verify["decision"] == 1
    assert "release.version: 1.2.3" in (proj / "AGENTS.md").read_text() and digest["ok"]
