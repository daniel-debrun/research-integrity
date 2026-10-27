import asyncio
import json

from mcp.client.client import Client

from research_ledger.server import build

TOOLS = {"add_source", "read_page", "cite", "claim", "verify", "list_entries"}


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
        assert t.annotations.read_only_hint or t.annotations.destructive_hint is False, t.name
    assert {t.name for t in tools if t.annotations.idempotent_hint} == {"add_source"}


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
