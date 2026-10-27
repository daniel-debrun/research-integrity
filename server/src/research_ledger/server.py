"""The ledger served over MCP (stdio). Every tool answers {"ok": true, ...} or a refusal {"ok": false, "error",
"meaning", "try"}; nothing is written unless its checks pass.

The ledger lives in $LEDGER_DIR, else `.ledger/` under the directory the server is started in (the project).
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .ledger import Ledger
from .quotecheck import Refusal

INSTRUCTIONS = """A ledger of the sources this project relies on, the passages quoted from them and the claims made
on those quotes. Record a document with add_source, read it with read_page, cite passages verbatim with the numbers
they state, and claim only on citations. A refusal says what is wrong and what to do instead; do what it says rather
than rewording to get past it. Run verify before reporting."""


def _safe(fn) -> dict:
    try:
        return fn()
    except Refusal as r:
        return r.envelope()


def _entry(e: dict) -> dict:
    return {"ok": True, "id": e["id"], "kind": e["kind"], "at": e["at"], **e["body"],
            **({"already": True} if e.get("already") else {})}


def build(root: str | os.PathLike | None = None) -> MCPServer:
    ledger = Ledger(root or os.environ.get("LEDGER_DIR") or Path.cwd() / ".ledger")
    server = MCPServer("research-ledger", instructions=INSTRUCTIONS, version="0.1.0")

    def reads(title):
        return ToolAnnotations(title=title, read_only_hint=True)

    def appends(title, **kw):
        return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=False, **kw)

    @server.tool(annotations=appends("Add a source", idempotent_hint=True, open_world_hint=True))
    def add_source(path_or_url: str) -> dict:
        """Record a document: a local PDF or text file, or an http(s) URL to one (fetched once). Its page texts are
        stored, so every later quote is checked against exactly what was read. The same bytes again return the
        existing source."""
        return _safe(lambda: _entry(ledger.add_source(path_or_url)))

    @server.tool(annotations=reads("Read a page"))
    def read_page(source: str, page: int, layout: bool = False) -> dict:
        """One page of a recorded source, as quotes are checked against it. `layout` keeps table rows whole (when
        the PDF reader provides it); quote a table row from that form."""
        def go():
            forms = ledger.forms(source)
            form = forms[1] if layout and len(forms) > 1 else forms[0]
            if not 1 <= page <= len(form):
                raise Refusal(f"the source has no page {page}", f"its pages are 1..{len(form)}")
            return {"ok": True, "source": source, "page": page, "n_pages": len(form), "text": form[page - 1]}
        return _safe(go)

    @server.tool(annotations=appends("Cite a passage"))
    def cite(source: str, page: int, quote: str, numbers: list[str | float] | None = None) -> dict:
        """Record a verbatim quote from one page of a source, with every number it is cited for. Refused unless the
        quote is on that page (a refusal names the page it is on) and writes each number (0.50 matches 0.5, 1,234
        matches 1234). Never paraphrase or join passages from different places."""
        return _safe(lambda: _entry(ledger.cite(source, page, quote, numbers or [])))

    @server.tool(annotations=appends("Make a claim"))
    def claim(text: str, citations: list[str]) -> dict:
        """Record a statement resting on citations (their ids from cite). Refused unless every number the statement
        writes is written in a quote it cites."""
        return _safe(lambda: _entry(ledger.claim(text, citations)))

    @server.tool(annotations=reads("Verify the ledger"))
    def verify() -> dict:
        """Recheck the whole ledger: every hash and link in the chain, every stored page, every citation's quote on
        its page, every claim's numbers in its quotes. Sources whose files are still there are read again and each
        citation rechecked against the document itself; `unread` lists those that could not be (URLs, moved files)."""
        return _safe(ledger.verify)

    @server.tool(annotations=reads("List the ledger"))
    def list_entries(kind: str = "") -> dict:
        """The ledger's entries, oldest first: sources, citations and claims (or one kind of them)."""
        rows = [_entry(e) for e in ledger.entries() if not kind or e["kind"] == kind]
        return {"ok": True, "ledger": str(ledger.root), "entries": rows}

    return server


def main() -> None:
    build().run("stdio")


if __name__ == "__main__":
    main()
