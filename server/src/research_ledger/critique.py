"""Critiques: a reviewer's findings on a draft, a claim, a run or a citation, and the main session's answer to each.

A critique is advice, not a verdict. Its findings are answered one by one by whoever owns the work, and an answer to
a blocking finding needs evidence recorded *after* the critique: a check made in response to it, not the reviewer's
word and not something already on file. That holds both ways: a finding is dismissed with the evidence that shows
the reviewer wrong, and upheld with the evidence that shows it right.

Kinds:
- draft: a file recorded at one moment (its sha256, its text in blobs/). A revision supersedes an earlier version
  and is refused until every blocking finding on that version is answered.
- critique: who wrote it (a persona), its target, and its findings F1, F2, ... Each has where it applies, what is
  wrong, whether it is blocking (it would change the conclusion or the plan), and the reviewer's own evidence (ids
  of citations or runs); a finding with none is marked unsupported.
- answer: one per finding, upheld or dismissed, with a reason and evidence.

`verify` adds: an upheld finding whose draft or claim still stands is a problem (supersede it), as is a standing
claim resting on a citation or run against which a finding was upheld; blocking findings not yet answered are listed.

Recording a draft also lists its wording to check: words that claim more than evidence can show ("novel", "proves").
They are listed, never refused: the draft is recorded as it is so the review can target that exact text, and a
draft that quotes someone else's "novel" claim is marked quoted, not penalised.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import quotecheck as qc
from .quotecheck import Refusal

VERDICTS = ("upheld", "dismissed")
TARGETS = ("draft", "claim", "run", "citation", "plan", "model")
EVIDENCE = ("citation", "run")

# Words a draft may not use on its own authority. Each: the rule, the pattern, what to write instead.
WORDING = (
    ("novelty", r"novel(?:ty)?|first to|no prior (?:work|stud(?:y|ies))|unprecedented",
     'never allowed: write "not found in the searched sources" and cite the search'),
    ("proof", r"prove[sdn]?|demonstrates? conclusively",
     "never allowed: say what the result supports, and with what interval"),
)
_WORDING = [(rule, re.compile(rf"\b(?:{pat})\b", re.I), need) for rule, pat, need in WORDING]
_QUOTED = re.compile(r'`[^`\n]*`|"[^"\n]*"|\u201c[^\u201d\n]*\u201d')
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def wording(text: str) -> list:
    """Each use of a word in WORDING: its rule, line, the word as written, the sentence around it, and whether it
    sits in quoted text (a blockquote, a code block, backticks or double quotes), where it is someone else's word."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        quoted_line = fenced or line.lstrip().startswith(">")
        spans = [m.span() for m in _QUOTED.finditer(line)]
        for rule, pat, need in _WORDING:
            for m in pat.finditer(line):
                start = m.start()
                sentence = next((x for x in _SENTENCE.split(line) if m.group(0) in x), line).strip()
                out.append({"rule": rule, "line": n, "word": m.group(0), "sentence": sentence[:300],
                            "quoted": quoted_line or any(a <= start < b for a, b in spans), "need": need})
    return out


def _findings(entries) -> dict:
    """Every finding by its ref (`critique:…#F1`): the critique, the finding, and its answer if it has one."""
    out = {}
    for e in entries:
        if e["kind"] == "critique":
            for f in e["body"]["findings"]:
                out[f"{e['id']}#{f['id']}"] = {"critique": e, "finding": f, "answer": None}
        elif e["kind"] == "answer":
            ref = f"{e['body']['critique']}#{e['body']['finding']}"
            if ref in out and out[ref]["answer"] is None:
                out[ref]["answer"] = e
    return out


def unanswered_blocking(entries, target: str) -> list:
    return [ref for ref, x in _findings(entries).items()
            if x["critique"]["body"]["target"] == target and x["finding"]["blocking"] and x["answer"] is None]


def gate(entries, target: str) -> None:
    """Refuse a revision of `target` while a blocking finding on it is unanswered, or when it is already revised."""
    open_ = unanswered_blocking(entries, target)
    if open_:
        raise Refusal(f"{open_[0]} is blocking and unanswered" + (f" ({len(open_) - 1} more)" if len(open_) > 1 else ""),
                      "a revision answers every blocking finding on the version it replaces",
                      "check each yourself and answer it with answer_finding, with evidence you record now")
    later = next((e for e in entries if e["body"].get("supersedes") == target), None)
    if later:
        raise Refusal(f"{target} is already superseded by {later['id']}", "a version is revised once",
                      f"revise {later['id']} instead")


def record_draft(led, path: str, supersedes: str = "") -> dict:
    """Record a file's text as a draft version, the numbers in it that no citation's quote writes, and its wording
    to check (see `wording`)."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise Refusal(f"no file at {path}", "a draft is a text file: an issue, a report, a plan, a paper")
    data = p.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal(f"{path} is not text", "a draft is recorded as UTF-8 text", "export it as text or markdown")
    sha = hashlib.sha256(data).hexdigest()
    entries = led.entries()
    known = next((e for e in entries if e["kind"] == "draft" and e["body"]["sha256"] == sha
                  and e["body"]["path"] == str(path)), None)
    drafts = {e["id"] for e in entries if e["kind"] == "draft"}
    quoted = set().union(*(qc.numbers(e["body"]["quote"]) for e in entries
                           if e["kind"] == "citation" and e["body"].get("source") not in drafts))
    untraced = sorted(qc.claim_numbers(text) - quoted)
    if known and (not supersedes or known["body"]["supersedes"] == supersedes):
        return {**known, "already": True, "untraced_numbers": [str(n) for n in untraced], "wording": wording(text)}
    old = None
    if supersedes:
        old = led.get(supersedes, "draft")
        if old["body"]["sha256"] == sha:
            raise Refusal(f"{path} is unchanged since {old['id']}", "a revision changes the text it replaces",
                          "edit the file, then record it")
        gate(entries, old["id"])
    answered = sorted(ref for ref, x in _findings(entries).items()
                      if old and x["critique"]["body"]["target"] == old["id"] and x["answer"])
    body = {"path": str(path), "sha256": sha, "text": led._put_blob(data),
            "version": old["body"]["version"] + 1 if old else 1, "supersedes": old["id"] if old else "",
            "answered": answered}
    return {**led._append("draft", body), "untraced_numbers": [str(n) for n in untraced], "wording": wording(text)}


def _evidence(led, ids, after: int = -1) -> list:
    out = []
    for i in ids or []:
        e = led.get(i)
        if e["kind"] not in EVIDENCE:
            raise Refusal(f"{i} is a {e['kind']}, not evidence", "evidence is a citation or a run",
                          "cite the passage or the run output that shows it")
        if e["seq"] <= after:
            raise Refusal(f"{i} was recorded before the critique", "an answer's evidence is a check made in response "
                          "to the finding, not the reviewer's or what was already on file",
                          "make the check now, record it, and cite that")
        out.append(e["id"])
    return out


def critique(led, target: str, findings: list, persona: str = "") -> dict:
    """Record a reviewer's findings on a draft, claim, run or citation."""
    t = led.get(target)
    if t["kind"] not in TARGETS:
        raise Refusal(f"a {t['kind']} is not critiqued", f"a critique targets a {', '.join(TARGETS)}")
    if not findings:
        raise Refusal("a critique has at least one finding", "if nothing broke, say so to the main session instead")
    items = []
    for n, f in enumerate(findings, 1):
        if not isinstance(f, dict) or not str(f.get("text", "")).strip():
            raise Refusal(f"finding {n} says nothing", "each finding says what is wrong")
        ev = _evidence(led, f.get("evidence"))
        items.append({"id": f"F{n}", "where": str(f.get("where", "")), "text": str(f["text"]).strip(),
                      "blocking": bool(f.get("blocking", False)), "evidence": ev, "unsupported": not ev})
    e = led._append("critique", {"target": t["id"], "persona": persona, "findings": items})
    return {**e, "blocking": sum(f["blocking"] for f in items), "unsupported": sum(f["unsupported"] for f in items)}


def answer(led, critique_id: str, finding: str, verdict: str, reason: str = "", evidence=()) -> dict:
    """Answer one finding, upheld or dismissed, with a reason and evidence recorded after the critique."""
    c = led.get(critique_id, "critique")
    f = next((x for x in c["body"]["findings"] if x["id"] == finding), None)
    if f is None:
        raise Refusal(f"{critique_id} has no finding {finding}",
                      f"its findings are {', '.join(x['id'] for x in c['body']['findings'])}")
    if verdict not in VERDICTS:
        raise Refusal(f"`{verdict}` is not a verdict", "a finding is upheld or dismissed")
    done = _findings(led.entries())[f"{c['id']}#{finding}"]["answer"]
    if done:
        raise Refusal(f"{finding} is already answered by {done['id']}", "a finding is answered once")
    ev = _evidence(led, evidence, after=c["seq"])
    if f["blocking"] and not ev:
        raise Refusal(f"{finding} is blocking, and is answered with your own evidence", "the reviewer's word is not "
                      "enough either way: check it yourself", "record the check (a citation or a run) and cite it")
    if verdict == "dismissed" and not reason.strip():
        raise Refusal("a dismissal says why", "the reason the finding does not hold")
    if not ev and not reason.strip():
        raise Refusal("an answer has a reason or evidence")
    return led._append("answer", {"critique": c["id"], "finding": finding, "verdict": verdict,
                                  "reason": reason.strip(), "evidence": ev})


def listing(led, target: str = "", open_only: bool = False) -> list:
    """Findings with their status, oldest first: on one target, or all."""
    rows = []
    for ref, x in _findings(led.entries()).items():
        c, f, a = x["critique"], x["finding"], x["answer"]
        if (target and c["body"]["target"] != target) or (open_only and a):
            continue
        rows.append({"ref": ref, "target": c["body"]["target"], "persona": c["body"]["persona"], **f,
                     "status": a["body"]["verdict"] if a else "open",
                     **({"answer": {k: a["body"][k] for k in ("reason", "evidence")}} if a else {})})
    return rows


# --- checking -------------------------------------------------------------------------------------------------------
def verify_entry(led, e: dict, seen: dict) -> None:
    b = e["body"]
    earlier = list(seen.values())
    if e["kind"] == "draft":
        if hashlib.sha256(led.blob(b["text"])).hexdigest() != b["sha256"]:
            raise Refusal("its text was changed")
        if b["supersedes"]:
            if seen.get(b["supersedes"], {}).get("kind") != "draft":
                raise Refusal(f"supersedes {b['supersedes']}, which is not an earlier draft")
            if unanswered_blocking(earlier, b["supersedes"]):
                raise Refusal("it was recorded while a blocking finding on the version it replaces was unanswered")
    elif e["kind"] == "critique":
        if b["target"] not in seen:
            raise Refusal(f"targets {b['target']}, which is not an earlier entry")
        for f in b["findings"]:
            if any(seen.get(i, {}).get("kind") not in EVIDENCE for i in f["evidence"]):
                raise Refusal(f"{f['id']} rests on an id that is not an earlier citation or run")
    elif e["kind"] == "answer":
        c = seen.get(b["critique"])
        if not c or c["kind"] != "critique":
            raise Refusal(f"answers {b['critique']}, which is not an earlier critique")
        f = next((x for x in c["body"]["findings"] if x["id"] == b["finding"]), None)
        if f is None:
            raise Refusal(f"answers {b['finding']}, which the critique does not have")
        if _findings(earlier)[f"{c['id']}#{f['id']}"]["answer"]:
            raise Refusal(f"answers {f['id']} a second time")
        if any(seen.get(i, {}).get("kind") not in EVIDENCE or seen[i]["seq"] <= c["seq"] for i in b["evidence"]):
            raise Refusal("its evidence is not a citation or run recorded after the critique")
        if f["blocking"] and not b["evidence"]:
            raise Refusal(f"answers blocking {f['id']} without evidence")


def summary(entries) -> dict:
    """After the walk: upheld findings left standing (problems), open blocking findings, each persona's record."""
    by_id = {e["id"]: e for e in entries}
    superseded = {e["body"].get("supersedes") for e in entries if e["kind"] in ("draft", "claim", "plan", "model")} - {"", None}
    problems, open_, personas = [], [], {}
    for ref, x in _findings(entries).items():
        c, f, a = x["critique"], x["finding"], x["answer"]
        tally = personas.setdefault(c["body"]["persona"] or "unnamed", {"upheld": 0, "dismissed": 0, "open": 0})
        tally[a["body"]["verdict"] if a else "open"] += 1
        if a is None and f["blocking"]:
            open_.append(ref)
        if not a or a["body"]["verdict"] != "upheld":
            continue
        t = by_id.get(c["body"]["target"])
        if t and t["kind"] in ("draft", "claim", "plan", "model") and t["id"] not in superseded:
            problems.append(f"{ref} was upheld, and {t['id']} still stands (supersede it with a revision)")
        elif t and t["kind"] in ("run", "citation"):
            for cl in entries:
                if cl["kind"] != "claim" or cl["id"] in superseded:
                    continue
                cites = [by_id.get(i) for i in cl["body"]["citations"]]
                if any(ci and (ci["id"] == t["id"] or ci["body"].get("run") == t["id"]) for ci in cites):
                    problems.append(f"{ref} was upheld against {t['id']}, and {cl['id']} still rests on it")
    return {"problems": problems, "open_findings": open_, "personas": personas}
