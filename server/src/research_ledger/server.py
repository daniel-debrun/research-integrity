"""The ledger served over MCP (stdio). Every tool answers {"ok": true, ...} or a refusal {"ok": false, "error",
"meaning", "try"}; nothing is written unless its checks pass.

The ledger lives in $LEDGER_DIR, else `.ledger/` under the directory the server is started in (the project).
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import critique as cq
from . import instruments as ins
from . import plans as pl
from .ledger import Ledger
from .quotecheck import Refusal

INSTRUCTIONS = """A ledger of the sources and instruments this project relies on, the passages quoted from them and
the claims made on those quotes. Record a document with add_source, read it with read_page, cite passages verbatim
with the numbers they state, and claim only on citations. A number you measure comes from an instrument: register the
program once, run it with run_instrument, and cite its output with cite_run. A draft is reviewed by recording it
with record_draft and having a reviewer record a critique; you then check each finding yourself and answer it with
answer_finding, with evidence you record for that check, before a revision is recorded. Before testing anything,
seal a plan with seal_plan and tag every run with its plan and hypothesis, so every trial counts. A refusal says what is wrong and what to do instead; do what it says rather
than rewording to get past it. Run verify before reporting."""


def _safe(fn) -> dict:
    try:
        return fn()
    except Refusal as r:
        return r.envelope()


_FIELDS = ("seq", "id", "kind", "at", "body", "prev", "hash")


def _entry(e: dict) -> dict:
    """An entry as a tool answers it: its id, kind and time, its body, and what the call added (`already`, ...)."""
    return {"ok": True, "id": e["id"], "kind": e["kind"], "at": e["at"], **e["body"],
            **{k: v for k, v in e.items() if k not in _FIELDS}}


def build(root: str | os.PathLike | None = None) -> MCPServer:
    ledger = Ledger(root or os.environ.get("LEDGER_DIR") or Path.cwd() / ".ledger")
    server = MCPServer("research-ledger", instructions=INSTRUCTIONS, version="0.1.3")

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
        """One page of a recorded source (or a draft, as page 1), as quotes are checked against it. `layout` keeps table rows whole (when
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
        """Record a verbatim quote from one page of a source, or from a draft (page 1), with every number it is
        cited for. Refused unless the
        quote is on that page (a refusal names the page it is on) and writes each number (0.50 matches 0.5, 1,234
        matches 1234). Never paraphrase or join passages from different places."""
        return _safe(lambda: _entry(ledger.cite(source, page, quote, numbers or [])))

    @server.tool(annotations=appends("Register an instrument", idempotent_hint=True))
    def register_instrument(name: str, command: str, source: str = "", secrets: list[str] | None = None,
                            outputs: list[str] | None = None, timeout: int = 600, acts: bool = False) -> dict:
        """Record a program whose output claims can rest on, as one exact version: a snapshot of the code in `source`
        (environments, caches, .env and key files are never taken) and a fingerprint of the program the command
        starts (for Python, its version and installed distributions). `command` is as you would type it in `source`;
        `secrets` are the names of environment variables it needs, passed through and masked in what is stored;
        `outputs` are globs of files a run writes in its working directory; `acts` marks an instrument that changes
        the outside world (an order, a message). The same code in the same environment returns the existing version;
        any change is a new one."""
        def go():
            e = ins.register(ledger, name, command, source, secrets or [], outputs or [], timeout, acts)
            b = e["body"]
            return {"ok": True, "id": e["id"], "ref": f"{b['name']}@{b['version']}", "argv": b["argv"],
                    "files": len(b["files"]), "runs_with": [f"{p['kind']}: {p['label']}" for p in b["env"]],
                    "secrets": b["secrets"], "outputs": b["outputs"], "acts": b["acts"],
                    **({"already": True} if e.get("already") else {})}
        return _safe(go)

    def _run(ref, args, acting, plan="", hypothesis="", look=""):
        def go():
            e = ins.run(ledger, ref, args or [], acting, pl.tag(ledger, plan, hypothesis, look))
            b = e["body"]
            return {"ok": True, "id": e["id"], "ref": b["ref"], "exit": b["exit"], "timed_out": b["timed_out"],
                    **{k: b[k] for k in pl.TAGS if b.get(k)},
                    "stdout_tail": e["stdout_tail"], "stderr_tail": e["stderr_tail"],
                    "outputs": {k: {**{f: o[f] for f in ("bytes", "kept")}, **({"text": e["shown"][k]}
                                if k in e["shown"] else {})} for k, o in b["outputs"].items()}}
        return _safe(go)

    @server.tool(annotations=appends("Run an instrument", open_world_hint=True))
    def run_instrument(ref: str, args: list[str] | None = None, plan: str = "", hypothesis: str = "",
                       look: str = "") -> dict:
        """Run a registered instrument (name@version) on `args` and record the run, failed or not. It runs a
        read-only copy of the registered code, never the live folder, and is refused, with nothing run, if what it
        runs with has changed since it was registered. `plan` and `hypothesis` tag it as a trial of that hypothesis
        (at start, never later); `look` spends a holdout look from look_holdout. Returns the exit code, the output
        tails and small text outputs in full; cite a figure from them with cite_run."""
        return _run(ref, args, False, plan, hypothesis, look)

    @server.tool(annotations=ToolAnnotations(title="Run an instrument that acts", read_only_hint=False,
                                             destructive_hint=True, open_world_hint=True))
    def run_acting_instrument(ref: str, args: list[str] | None = None, plan: str = "", hypothesis: str = "",
                              look: str = "") -> dict:
        """Run a registered instrument that acts on the outside world (registered with acts), as run_instrument
        does."""
        return _run(ref, args, True, plan, hypothesis, look)

    @server.tool(annotations=appends("Seal a plan"))
    def seal_plan(name: str, question: str, hypotheses: list[dict], holdout: list[str] | None = None) -> dict:
        """Record what a study will test before the runs it governs: the question, and hypotheses, each
        {"statement": what it claims, "metric": what is measured, "success": what counts as a result, "comparison":
        against what (optional)}, numbered H1, H2, ... `holdout` lists files the work must not look at until a
        look_holdout; they are recorded by sha256. A sealed plan changes only by amend_plan."""
        return _safe(lambda: _entry(pl.seal(ledger, name, question, hypotheses, holdout or [])))

    @server.tool(annotations=appends("Amend a plan"))
    def amend_plan(name: str, reason: str, question: str = "", hypotheses: list[dict] | None = None,
                   holdout: list[str] | None = None, because: list[str] | None = None) -> dict:
        """Record a plan's next version: what changes (a new question, the full new list of hypotheses, keeping
        an existing hypothesis's id to keep its trials, or a new holdout), why, and what prompted it (`because`:
        finding refs, run or citation ids). Refused while a blocking finding on the current version is
        unanswered."""
        return _safe(lambda: _entry(pl.amend(ledger, name, reason, question, hypotheses, holdout, because or [])))

    @server.tool(annotations=reads("Plan status"))
    def plan_status(name: str) -> dict:
        """A plan's trials: for each hypothesis, every run tagged to it (succeeded, failed, still running), how
        many a claim reports, and the successful ones nothing reports; untagged runs of the same instruments since
        the seal; its amendments and its holdout looks."""
        return _safe(lambda: {"ok": True, **pl.status(ledger.entries(), name)})

    @server.tool(annotations=ToolAnnotations(title="Look at a holdout", read_only_hint=False, destructive_hint=True))
    def look_holdout(plan: str, reason: str) -> dict:
        """Spend one look at a plan's holdout, with the reason. Refused if a holdout file changed since the seal.
        Pass the look's id to the one run that reads the holdout; a look whose run fails stays spent. Looks are
        counted in plan_status."""
        return _safe(lambda: _entry(pl.look(ledger, plan, reason)))

    @server.tool(annotations=appends("Cite a run's output"))
    def cite_run(run: str, quote: str, numbers: list[str | float] | None = None, file: str = "stdout") -> dict:
        """Record a verbatim quote from a run's output (`file`: stdout, stderr or one of its text outputs), with
        every number it is cited for. Refused unless the quote is there and writes each number."""
        return _safe(lambda: _entry(ledger.cite_run(run, quote, numbers or [], file)))

    @server.tool(annotations=appends("Make a claim"))
    def claim(text: str, citations: list[str], supersedes: str = "") -> dict:
        """Record a statement resting on citations (their ids from cite or cite_run). Refused unless every number
        the statement writes is written in a quote it cites. `supersedes` replaces an earlier claim; refused while a
        blocking finding on it is unanswered."""
        return _safe(lambda: _entry(ledger.claim(text, citations, supersedes)))

    @server.tool(annotations=appends("Record a draft", idempotent_hint=True))
    def record_draft(path: str, supersedes: str = "") -> dict:
        """Record a text file (an issue, a report, a plan, a paper) as a draft version, so a critique can target
        exactly that text. `supersedes` makes it the revision of an earlier version, refused until every blocking
        finding on that version is answered. Lists the numbers in it that no citation's quote writes."""
        return _safe(lambda: _entry(cq.record_draft(ledger, path, supersedes)))

    @server.tool(annotations=appends("Record a critique"))
    def record_critique(target: str, findings: list[dict], persona: str = "") -> dict:
        """Record a reviewer's findings on a draft, claim, run or citation (its id). Each finding is {"text": what
        is wrong, "where": the line, quote or id it applies to, "blocking": true if it would change the conclusion
        or the plan, "evidence": ids of citations or runs you recorded that show it}. A finding with no evidence is
        marked unsupported. Findings are answered by the work's owner, not by the reviewer."""
        return _safe(lambda: _entry(cq.critique(ledger, target, findings, persona)))

    @server.tool(annotations=appends("Answer a finding"))
    def answer_finding(critique: str, finding: str, verdict: str, reason: str = "",
                       evidence: list[str] | None = None) -> dict:
        """Answer one finding of a critique (F1, F2, ...): `upheld` or `dismissed`. A blocking finding needs
        evidence either way, ids of citations or runs recorded after the critique: check it yourself, record that
        check, and cite it. A dismissal needs a reason."""
        return _safe(lambda: _entry(cq.answer(ledger, critique, finding, verdict, reason, evidence or [])))

    @server.tool(annotations=reads("List findings"))
    def list_findings(target: str = "", open_only: bool = False) -> dict:
        """Critiques' findings with their status (open, upheld, dismissed): on one target, or all."""
        return _safe(lambda: {"ok": True, "findings": cq.listing(ledger, target, open_only)})

    @server.tool(annotations=reads("Verify the ledger"))
    def verify() -> dict:
        """Recheck the whole ledger: every hash and link in the chain, every stored page, every citation's quote on
        its page, every claim's numbers in its quotes. Sources whose files are still there are read again and each
        citation rechecked against the document itself; `unread` lists those that could not be (URLs, moved files).
        Instruments' code and runs' stored outputs are checked against their hashes; runs are not run again."""
        return _safe(ledger.verify)

    @server.tool(annotations=reads("List the ledger"))
    def list_entries(kind: str = "") -> dict:
        """The ledger's entries, oldest first, or one kind: source, instrument, run, citation, claim, draft,
        critique, answer, node, launch."""
        rows = [_entry(e) for e in ledger.entries() if not kind or e["kind"] == kind]
        return {"ok": True, "ledger": str(ledger.root), "entries": rows}

    return server


def main() -> None:
    build().run("stdio")


if __name__ == "__main__":
    main()
