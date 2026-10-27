import asyncio
import json
import sys

from mcp.client.client import Client

from research_ledger.server import build

TOOLS = {"add_source", "read_page", "cite", "claim", "verify", "list_entries", "register_instrument",
         "run_instrument", "run_acting_instrument", "cite_run"}


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
    assert {t.name for t in tools if t.annotations.idempotent_hint} == {"add_source", "register_instrument"}
    assert {t.name for t in tools if t.annotations.destructive_hint} == {"run_acting_instrument"}


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
