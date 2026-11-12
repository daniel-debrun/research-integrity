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
from . import memory as mem
from . import report as rp
from . import meta as mt
from . import search as sr
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
    server = MCPServer("research-ledger", instructions=INSTRUCTIONS, version="0.3.0")

    def reads(title):
        return ToolAnnotations(title=title, read_only_hint=True)

    def appends(title, **kw):
        return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=False, **kw)

    @server.tool(annotations=appends("Add a source", idempotent_hint=True, open_world_hint=True))
    def add_source(path_or_url: str, source_type: str = "", doi: str = "") -> dict:
        """Record a document: a local PDF or text file, or an http(s) URL to one (fetched once). Its page texts are
        stored, so every later quote is checked against exactly what was read. The same bytes again return the
        existing source. `source_type` is primary, paper, vendor, secondary or aggregator (vendor and aggregator
        pages go stale after 90 days). `doi` asks Crossref for retraction and correction notices on it."""
        def go():
            e = ledger.add_source(path_or_url)
            out = _entry(e)
            if source_type or doi:
                m = mt.annotate(ledger, e["id"], source_type, None, doi)
                out["meta"] = m["body"]
            return out
        return _safe(go)

    @server.tool(annotations=appends("Annotate a source", open_world_hint=True))
    def annotate_source(source: str, source_type: str = "", stale_after_days: int | None = None,
                        doi: str = "") -> dict:
        """Give a recorded source its type (primary, paper, vendor, secondary, aggregator), a freshness window in
        days, or a DOI to check at Crossref for retraction and correction notices. Recorded as a new entry; the
        source itself is never rewritten."""
        return _safe(lambda: _entry(mt.annotate(ledger, source, source_type, stale_after_days, doi)))

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

    @server.tool(annotations=reads("Search the sources"))
    def search_sources(query: str, k: int = 8, sources: list[str] | None = None) -> dict:
        """Passages of the recorded sources ranked against a question (BM25 over words), each with its source,
        page and the numbers it writes, to read and quote. It finds candidates and checks nothing: a passage enters
        the ledger only through cite. Search before writing, and abstain where no passage states the number."""
        return _safe(lambda: {"ok": True, **sr.search(ledger, query, k, tuple(sources or ()))})

    @server.tool(annotations=appends("Cite a passage"))
    def cite(source: str, page: int, quote: str, numbers: list[str | float] | None = None,
             casefold: bool = False) -> dict:
        """Record a verbatim quote from one page of a source, or from a draft (page 1), with every number it is
        cited for. Refused unless the
        quote is on that page (a refusal names the page it is on) and writes each number (0.50 matches 0.5, 1,234
        matches 1234). A quote that starts or ends inside a longer number is refused. `casefold` matches letters
        regardless of case. Never paraphrase or join passages from different places."""
        return _safe(lambda: _entry(ledger.cite(source, page, quote, numbers or [], casefold)))

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

    @server.tool(annotations=appends("Reproduce a run", open_world_hint=True))
    def reproduce_run(run: str, look: str = "") -> dict:
        """Run a recorded run again (the same instrument version, the same args, the same way) and record whether
        it came out the same: exit code and every declared output by sha256 decide, stdout stands in when nothing
        is declared, stderr is shown but never decides. The rerun is recorded as a run marked `reproduces` and
        counts as no trial. Refused for an instrument that acts or ran on a node, and for a run that spent a
        holdout look unless `look` spends a new one. Answers with the verdict and the first differing line of each
        output that differs."""
        def go():
            orig = ledger.get(run, "run")
            e = ins.reproduce(ledger, run, pl.spend(ledger, look, orig) if look else None)
            r = e["run"]
            return {"ok": True, "id": e["id"], "of": e["body"]["of"], "by": r["id"], "verdict": e["body"]["verdict"],
                    **{k: e["body"][k] for k in ("exit", "stdout", "stderr", "outputs")},
                    "differences": e["differences"], "exit_code": r["body"]["exit"],
                    "stdout_tail": r["stdout_tail"], "stderr_tail": r["stderr_tail"]}
        return _safe(go)

    @server.tool(annotations=appends("Seal a plan"))
    def seal_plan(name: str, question: str, hypotheses: list[dict], holdout: list[str] | None = None,
                  continues: str = "", prior_trials: dict | None = None, subject: str = "") -> dict:
        """Record what a study will test before the runs it governs: the question, and hypotheses, each
        {"statement": what it claims, "metric": what is measured, "success": what counts as a result, "comparison":
        against what (optional)}, numbered H1, H2, ... `holdout` lists files the work must not look at until a
        look_holdout; they are recorded by sha256. A sealed plan changes only by amend_plan. A trial count belongs
        to the question: sealing a question an earlier plan asks is refused unless `continues` names that plan (its
        id), and then a hypothesis given one of its ids ("id": "H1") inherits that id's trials. In a fresh ledger,
        `prior_trials` declares the trials counted elsewhere: {"from": that ledger's head hash, URL or path, "H1":
        12, ...}; it is your word, recorded as such. `subject` keys the question (`loss.ablation`): another plan on the
        same subject is refused like the same question, however it is worded."""
        return _safe(lambda: _entry(pl.seal(ledger, name, question, hypotheses, holdout or [], continues,
                                            prior_trials, subject)))

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
        """A plan's trials: for each hypothesis, every run tagged to it (succeeded, failed, still running), the
        trials inherited from the plans it continues and declared from another ledger, how many a claim reports, and
        the successful ones nothing reports; untagged runs of the same instruments since the seal; its amendments,
        its lineage and its holdout looks."""
        return _safe(lambda: {"ok": True, **pl.status(ledger.entries(), name)})

    @server.tool(annotations=ToolAnnotations(title="Look at a holdout", read_only_hint=False, destructive_hint=True))
    def look_holdout(plan: str, reason: str) -> dict:
        """Spend one look at a plan's holdout, with the reason. Refused if a holdout file changed since the seal.
        Pass the look's id to the one run that reads the holdout; a look whose run fails stays spent. Looks are
        counted in plan_status."""
        return _safe(lambda: _entry(pl.look(ledger, plan, reason)))

    @server.tool(annotations=appends("Cite a run's output"))
    def cite_run(run: str, quote: str, numbers: list[str | float] | None = None, file: str = "stdout",
                 casefold: bool = False) -> dict:
        """Record a verbatim quote from a run's output (`file`: stdout, stderr or one of its text outputs), with
        every number it is cited for. Refused unless the quote is there and writes each number."""
        return _safe(lambda: _entry(ledger.cite_run(run, quote, numbers or [], file, casefold)))

    @server.tool(annotations=appends("Make a claim"))
    def claim(text: str, citations: list[str], supersedes: str = "", subject: str = "") -> dict:
        """Record a statement resting on citations (their ids from cite or cite_run). Refused unless every number
        the statement writes is one its citations were cited for (a label's number, such as Table 4, aside). `supersedes` replaces an earlier claim; refused while a
        blocking finding on it is unanswered. Answers with `wording` when the statement uses a word it should not
        ("proves", "novel"); the claim is recorded all the same. `subject` keys what the claim is about
        (highbyte.price.per-site-year): standing claims on one subject that write different numbers come back as
        `conflicts`; the ledger reports them and does not choose."""
        return _safe(lambda: _entry(ledger.claim(text, citations, supersedes, subject)))

    @server.tool(annotations=appends("Record a draft", idempotent_hint=True))
    def record_draft(path: str, supersedes: str = "") -> dict:
        """Record a text file (an issue, a report, a plan, a paper) as a draft version, so a critique can target
        exactly that text. `supersedes` makes it the revision of an earlier version, refused until every blocking
        finding on that version is answered. Lists the numbers in it that no citation's quote writes, and its
        `wording` to check: "novel", "proves" and the like, each with its line and whether it is inside a quote (then
        it is someone else's word). Listed, not refused; a reviewer makes a finding of each unquoted one."""
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

    @server.tool(annotations=appends("Record a fact"))
    def record_fact(probe: str, target: str, expect: str | None = None, note: str = "", text: str = "",
                    key: str = "", ref: str = "", ancestor_of: str = "", casefold: bool = False) -> dict:
        """Record a fact about the machine that the server checks itself, and checks again whenever it is recalled.
        `probe` is one of: file (target: a path; records sha256 and size), text_in_file (target, `text`: exact, at
        least 4 characters, never cut inside a word or number), field (target: a JSON or TOML file, `key`: dotted,
        such as project.version; keys naming secrets are refused), git_ref (target: a repository, `ref`, optional
        `ancestor_of`), run (target: a cite_run citation id). `expect` refuses the fact if the probe sees otherwise.
        The fact's statement is written from what the probe saw; `note` is kept, marked unchecked. When asked to
        remember something a probe can see (a version, a config value, a file, a commit), record it here rather than
        in a memory note: a note is never checked again, a fact is checked on every recall."""
        return _safe(lambda: _entry(mem.record_fact(ledger, probe, target, expect, note, text, key, ref, ancestor_of,
                                                    casefold)))

    @server.tool(annotations=reads("Recall"))
    def recall(query: str, kinds: list[str] | None = None, rerun: bool = False) -> dict:
        """Find facts, decisions and claims by the words they share with `query`, newest and closest first. Each
        fact is probed again: current, stale (with what the probe sees now), unreachable, or as_of for a run fact
        (pass `rerun` to run its instrument again, which records a run). Superseded decisions and claims are left
        out. The search only finds; the probes check."""
        return _safe(lambda: {"ok": True, **mem.recall(ledger, query, tuple(kinds or ("fact", "decision", "claim")),
                                                      rerun)})

    @server.tool(annotations=appends("Record a decision"))
    def decide(subject: str, question: str, choice: str, reason: str, evidence: list[str] | None = None,
               supersedes: str = "", new_subject: bool = False) -> dict:
        """Record a decision on a subject key (such as firm.status or release.split): the question, the choice, why,
        and the entries it rests on. Refused on a subject the ledger does not have unless `new_subject` (the refusal
        lists subjects that look like it: use one if it fits), and refused on a subject with a standing decision
        unless `supersedes` names it. Current decisions are printed at each session's start."""
        return _safe(lambda: _entry(mem.decide(ledger, subject, question, choice, reason, evidence or [], supersedes,
                                               new_subject)))

    @server.tool(annotations=reads("List subjects"))
    def list_subjects() -> dict:
        """Every subject key in the ledger (decisions, plans, claims), with where it was first used."""
        return _safe(lambda: {"ok": True, "subjects": list(mem.subjects(ledger.entries()).values())})

    @server.tool(annotations=reads("List decisions"))
    def list_decisions(current_only: bool = True, days: int = 14) -> dict:
        """The decision standing on each subject, and those superseded in the last `days`, each with its
        replacement."""
        return _safe(lambda: {"ok": True, **mem.list_decisions(ledger.entries(), current_only, days)})

    @server.tool(annotations=appends("Write the digest", idempotent_hint=True))
    def write_digest(path: str, days: int = 14) -> dict:
        """Write current decisions, recent supersessions and stale facts into a marked block of a markdown file, for
        agents that read an instruction file (AGENTS.md for Codex, GEMINI.md for Gemini CLI) instead of the
        session-start digest. Only the block is replaced; nothing else in the file is touched. Runs only when
        called. Use it rather than copying decisions into the file by hand: a hand-written copy has no markers, so
        the next write_digest can't replace it and it goes stale."""
        return _safe(lambda: mem.write_digest(ledger, path, days))

    @server.tool(annotations=appends("Check a report"))
    def check_report(path_or_text: str, cwd: str = "") -> dict:
        """Check every path, commit, branch, tag, version and test count an agent's report states, against the
        machine: confirmed, contradicted (with what is there), missing, refused, ambiguous or uncheckable. Run it on
        a subagent's hand-back before relying on it. A version is checked against what is installed when the
        sentence says installed, enabled or running; against the repository's manifests when it says declares, in
        the repo or bumped; otherwise against both. The result is recorded with the report's sha256."""
        return _safe(lambda: {"ok": True, **rp.check_report(ledger, path_or_text, cwd)})

    @server.tool(annotations=reads("Verify the ledger"))
    def verify(anchor: str = "") -> dict:
        """Recheck the whole ledger: every hash and link in the chain, every stored page, every citation's quote on
        its page, every claim's numbers in its quotes. Sources whose files are still there are read again and each
        citation rechecked against the document itself; `unread` lists those that could not be (URLs, moved files).
        Instruments' code and runs' stored outputs are checked against their hashes, and each reproduction's
        comparison is recomputed from them; nothing is run again here (reproduce_run does that). `anchor` names a file
        written by anchor_ledger: an entry removed from the end, or a chain rewritten, shows up against it."""
        return _safe(lambda: ledger.verify(anchor))

    @server.tool(annotations=appends("Anchor the ledger"))
    def anchor_ledger(path: str) -> dict:
        """Append the chain's head (its length and last hash) to a file outside the ledger. Keep that file where
        the ledger's writer cannot quietly change it (commit it elsewhere, send it, timestamp it); verify with
        `anchor` then shows entries removed from the end or a rewritten chain."""
        return _safe(lambda: ledger.anchor(path))

    @server.tool(annotations=reads("List the ledger"))
    def list_entries(kind: str = "") -> dict:
        """The ledger's entries, oldest first, or one kind: source, instrument, run, reproduction, citation, claim,
        draft, critique, answer, node, launch, plan, look, model, verdict."""
        rows = [_entry(e) for e in ledger.entries() if not kind or e["kind"] == kind]
        return {"ok": True, "ledger": str(ledger.root), "entries": rows}

    return server


def main() -> None:
    import sys
    args = sys.argv[1:]
    if args[:1] == ["--digest"]:  # the session-start hook: what is decided and what no longer holds
        root = Path(os.environ.get("LEDGER_DIR") or Path.cwd() / ".ledger")
        if (root / "chain.jsonl").exists():
            text = mem.digest(Ledger(root))
            if text:
                print(text)
        print(mem.USAGE)
        return
    if args[:1] == ["--write-digest"] and len(args) == 2:
        root = Path(os.environ.get("LEDGER_DIR") or Path.cwd() / ".ledger")
        print(_safe(lambda: mem.write_digest(Ledger(root), args[1])))
        return
    build().run("stdio")


if __name__ == "__main__":
    main()
