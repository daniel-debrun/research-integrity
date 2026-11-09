import asyncio
import json
import sys
from pathlib import Path

from mcp.client.client import Client

from research_ledger import compute_server
from research_ledger.server import build

TOOLS = {"add_source", "read_page", "cite", "claim", "verify", "list_entries", "register_instrument", "reproduce_run",
         "run_instrument", "run_acting_instrument", "cite_run", "record_draft", "record_critique", "answer_finding",
         "list_findings", "seal_plan", "amend_plan", "plan_status", "look_holdout"}
COMPUTE = {"list_nodes", "register_node", "register_instrument_on", "launch_instrument", "wait_run", "run_logs",
           "stop_run", "runs_in_flight"}
ROOT = Path(__file__).resolve().parents[2]
PREFIX = "mcp__plugin_research-integrity_research-ledger__"


def run(server, calls):
    async def go():
        async with Client(server) as c:
            tools = (await c.list_tools()).tools
            out = []
            for name, args in calls:
                r = await c.call_tool(name, args)
                out.append(json.loads(r.content[0].text))
            return tools, out
    return asyncio.run(go())


def test_every_tool_is_annotated_for_the_directory(tmp_path):
    tools, _ = run(build(tmp_path / ".ledger"), [])
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.title and t.annotations.read_only_hint is not None, t.name
        assert t.annotations.read_only_hint or t.annotations.destructive_hint is not None, t.name
    assert {t.name for t in tools if t.annotations.idempotent_hint} == {"add_source", "register_instrument",
                                                                          "record_draft"}
    assert {t.name for t in tools if t.annotations.destructive_hint} == {"run_acting_instrument", "look_holdout"}


def test_a_refusal_comes_back_as_its_envelope_and_a_good_citation_is_recorded(tmp_path, paper):
    server = build(tmp_path / ".ledger")
    _, (src,) = run(server, [("add_source", {"path_or_url": str(paper)})])
    _, (bad, page, good, v) = run(server, [
        ("cite", {"source": src["id"], "page": 1, "quote": "Site calibration 0.70"}),
        ("read_page", {"source": src["id"], "page": 2}),
        ("cite", {"source": src["id"], "page": 2, "quote": "Site calibration 0.70", "numbers": ["0.7"]}),
        ("verify", {})])
    assert bad == {"ok": False, "error": "the quote is not on page 1", "meaning": "it is on page 2",
                   "try": "cite the page it is on"}
    assert "Site calibration 0.70" in page["text"] and good["ok"] and good["id"].startswith("citation:")
    assert v["ok"] and v["citation"] == 1


def test_an_instrument_is_registered_run_and_cited_over_mcp(tmp_path):
    src = tmp_path / "tool"
    src.mkdir()
    (src / "measure.py").write_text('print("mean skill 0.71 at 12 stations")')
    server = build(tmp_path / ".ledger")
    _, (reg, again) = run(server, [("register_instrument", {"name": "skill", "command": f"{sys.executable} measure.py",
                                                            "source": str(src)})] * 2)
    _, (r, c, v) = run(server, [("run_instrument", {"ref": reg["ref"]}), ("cite_run", {"run": "?", "quote": "x"}),
                                ("verify", {})])
    assert reg["ok"] and reg["files"] == 1 and again["already"] and again["ref"] == reg["ref"]
    assert r["exit"] == 0 and "mean skill 0.71" in r["stdout_tail"] and c["ok"] is False and v["ok"]
    _, (good,) = run(server, [("cite_run", {"run": r["id"], "quote": "mean skill 0.71 at 12 stations",
                                            "numbers": ["0.71"]})])
    assert good["ok"] and good["run"] == r["id"]


def test_the_compute_server_is_annotated_and_only_stop_is_destructive(tmp_path):
    tools, _ = run(compute_server.build(tmp_path / ".ledger"), [])
    assert {t.name for t in tools} == COMPUTE
    assert all(t.annotations.title and t.annotations.open_world_hint for t in tools)
    assert {t.name for t in tools if t.annotations.destructive_hint} == {"stop_run"}


def agent_tools(name):
    head = (ROOT / "agents" / f"{name}.md").read_text().split("---")[1]
    line = next(l for l in head.splitlines() if l.startswith("tools:"))
    return {t.strip() for t in line[len("tools:"):].split(",")}


def test_personas_name_real_tools_and_reviewers_cannot_settle_or_edit():
    for name in ("adversary", "reviewer", "scout", "replicator", "experimenter"):
        ledger_tools = {t[len(PREFIX):] for t in agent_tools(name) if t.startswith(PREFIX)}
        assert ledger_tools and ledger_tools <= TOOLS, name
        assert "answer_finding" not in ledger_tools and "run_acting_instrument" not in ledger_tools, name
    for name in ("adversary", "reviewer"):
        tools = agent_tools(name)
        assert not tools & {"Write", "Edit", "Bash", PREFIX + "record_draft", PREFIX + "claim"}, name
        assert PREFIX + "record_critique" in tools, name
