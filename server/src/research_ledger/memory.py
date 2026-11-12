"""Memory: facts about the machine that the server re-probes when they are recalled, and decisions that name what
they replace.

A `fact` is the result of a read-only probe the server runs itself; the agent names the probe and its target, never
the value seen. Its statement is written from the probe, so no wording rides on a probe that checks something else.
Probes:

- file: a file exists, with its size and sha256.
- text_in_file: a string is in a file: exact, line endings aside, at least 4 characters, never starting or ending
  inside a word or a number; letters regardless of case only when `casefold` is given.
- field: the value at a key of a JSON or TOML file (a scalar). A key that names a secret is refused.
- git_ref: what a ref of a repository resolves to, and whether it is an ancestor of another ref.
- run: a quote from an instrument run's output (a `cite_run` citation). Not re-run on recall unless asked: it is
  shown `as_of` the run's time.

`recall` finds facts, decisions and claims by their words (no embeddings: the search only finds candidates, it
checks nothing) and re-probes each fact: `current`, `stale` (with what the probe sees now), `unreachable` (gone, or
now refused by the guard) or `as_of`. A stale fact is shown with its flag, never dropped.

A `decision` answers a question on a subject key (`firm.status`). Subjects are a registry: a decision on a subject
the ledger does not have is refused unless declared `new_subject`, and the refusal shows the subjects that look like
it. A decision on a subject with a current decision is refused unless it `supersedes` that decision.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import guard
from . import plans as pl
from . import quotecheck as qc
from .quotecheck import Refusal

PROBES = ("file", "text_in_file", "field", "git_ref", "run")
EVIDENCE = ("claim", "run", "fact", "report", "citation", "decision", "source")
MIN_TEXT = 4
_WORD = re.compile(r"[a-z0-9][a-z0-9_.@-]*")


# --- probes -----------------------------------------------------------------------------------------------------------
class Unreachable(Exception):
    """The target is gone, unreadable or refused: the fact can be neither confirmed nor contradicted."""


def _file(target: str) -> Path:
    try:
        path = guard.check(target)
    except Refusal as r:
        raise Unreachable(r.error)
    if not path.is_file():
        raise Unreachable(f"no file at {target}")
    return path


def text_holds(text: str, s: str, casefold: bool = False) -> bool:
    """Whether `s` is in `text` exactly (line endings aside), not starting or ending inside a word or a number."""
    t, s = text.replace("\r\n", "\n"), s.replace("\r\n", "\n")
    if casefold:
        t, s = t.casefold(), s.casefold()
    i = t.find(s)
    while i != -1:
        j = i + len(s)
        before = t[i - 1] if i else ""
        after = t[j] if j < len(t) else ""
        cut = (s[0].isalnum() and before.isalnum()) or (s[-1].isalnum() and after.isalnum()) or \
            (s[-1].isdigit() and after in ".," and t[j + 1:j + 2].isdigit()) or \
            (s[0].isdigit() and before in ".," and i > 1 and t[i - 2].isdigit())
        if not cut:
            return True
        i = t.find(s, i + 1)
    return False


def _load(path: Path):
    data = path.read_bytes()
    if path.suffix.lower() == ".toml":
        try:
            import tomllib
        except ImportError:  # Python 3.10
            import tomli as tomllib
        return tomllib.loads(data.decode("utf-8"))
    return json.loads(data.decode("utf-8"))


def _at(obj, key: str):
    """The value at a dotted key; a list is indexed by number (`plugins.name@x.0.version`)."""
    for part in _split_key(key):
        if isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        elif isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            raise Unreachable(f"no key {key}")
    if isinstance(obj, (dict, list)):
        raise Unreachable(f"{key} holds a {type(obj).__name__}, not a single value")
    return obj


def _split_key(key: str) -> list:
    """A dotted key whose parts may hold dots when bracketed: `plugins["a@b.c"].0.version`."""
    out, i = [], 0
    for m in re.finditer(r'\["([^"]+)"\]|([^.\[\]]+)', key):
        out.append(m.group(1) or m.group(2))
        i = m.end()
    return out


def _git(repo: str, *args) -> str:
    path = Path(repo).expanduser()
    if not path.is_dir():
        raise Unreachable(f"no folder at {repo}")
    done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=30)
    if done.returncode not in (0, 1):
        raise Unreachable(done.stderr.strip()[:200] or "git failed")
    return done.stdout.strip() if done.returncode == 0 else ""


def observe(led, probe: str, target: str, args: dict, rerun: bool = False) -> dict:
    """What the probe sees now. Raises Unreachable when it cannot see."""
    if probe == "file":
        path = _file(target)
        import hashlib
        return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
    if probe == "text_in_file":
        path = _file(target)
        return {"present": text_holds(path.read_text(encoding="utf-8", errors="replace"), args["text"],
                                      args.get("casefold", False))}
    if probe == "field":
        try:
            return {"value": _at(_load(_file(target)), args["key"])}
        except (ValueError, UnicodeDecodeError) as e:
            raise Unreachable(f"{target} could not be parsed: {str(e)[:120]}")
    if probe == "git_ref":
        sha = _git(target, "rev-parse", "--verify", "--quiet", args["ref"] + "^{commit}")
        if not sha:
            raise Unreachable(f"{args['ref']} does not resolve in {target}")
        out = {"commit": sha}
        if args.get("ancestor_of"):
            done = subprocess.run(["git", "-C", str(Path(target).expanduser()), "merge-base", "--is-ancestor", sha,
                                   args["ancestor_of"]], capture_output=True, timeout=30)
            out["ancestor"] = done.returncode == 0
        return out
    if probe == "run":
        c = led.get(target, "citation")
        if "run" not in c["body"]:
            raise Refusal(f"{target} quotes a source, not a run", "a run fact rests on a cite_run citation")
        r = led.get(c["body"]["run"], "run")
        if not rerun:
            return {"run": r["id"], "as_of": r["at"], "quote": c["body"]["quote"]}
        from . import instruments as ins
        inst = led.get(r["body"]["instrument"], "instrument")["body"]
        new = ins.run(led, f"{inst['name']}@{inst['version']}", r["body"].get("args", []))
        try:
            qc.check([[ins.output_text(led, new, c["body"]["file"])]], 1, c["body"]["quote"], c["body"]["numbers"],
                     bool(c["body"].get("casefold")))
            holds = True
        except Refusal:
            holds = False
        return {"run": new["id"], "as_of": new["at"], "quote": c["body"]["quote"], "holds": holds}
    raise Refusal(f"`{probe}` is not a probe", f"the probes are {', '.join(PROBES)}")


def statement(probe: str, target: str, args: dict, seen: dict) -> str:
    """The fact's words, written from the probe and what it saw."""
    if probe == "file":
        return f"{target} exists with sha256 {seen['sha256']} ({seen['size']} bytes)"
    if probe == "text_in_file":
        return f"{target} contains {json.dumps(args['text'])}" + (" (any case)" if args.get("casefold") else "")
    if probe == "field":
        return f"{target} has {args['key']} = {json.dumps(seen['value'])}"
    if probe == "git_ref":
        s = f"{args['ref']} in {target} is {seen['commit']}"
        if args.get("ancestor_of"):
            s += f", {'an' if seen['ancestor'] else 'not an'} ancestor of {args['ancestor_of']}"
        return s
    return f"run {seen['run']} ({seen['as_of']}) printed {json.dumps(seen['quote'])}"


def _main(probe: str, seen: dict):
    """The part of an observation that decides whether a fact still holds."""
    return {k: v for k, v in seen.items() if k not in ("as_of", "run")}


def _args(probe: str, text: str, key: str, ref: str, ancestor_of: str, casefold: bool) -> dict:
    if probe == "text_in_file":
        if len(text.strip()) < MIN_TEXT:
            raise Refusal(f"the text is too short to check ({len(text.strip())} characters)",
                          f"fewer than {MIN_TEXT} characters are found almost anywhere", "give a longer string")
        return {"text": text, **({"casefold": True} if casefold else {})}
    if probe == "field":
        if not key:
            raise Refusal("a field probe needs its key", "a dotted key such as version, or project.version")
        bad = [p for p in _split_key(key) if guard.secret_key(p)]
        if bad:
            raise Refusal(f"the key {key} names a secret ({bad[0]})", "a secret's value is never recorded",
                          "probe another key")
        return {"key": key}
    if probe == "git_ref":
        if not ref:
            raise Refusal("a git_ref probe needs its ref", "a branch, tag or commit")
        return {"ref": ref, **({"ancestor_of": ancestor_of} if ancestor_of else {})}
    return {}


def record_fact(led, probe: str, target: str, expect=None, note: str = "", text: str = "", key: str = "",
                ref: str = "", ancestor_of: str = "", casefold: bool = False) -> dict:
    """Run a probe and record what it saw as a fact. Refused when it cannot see, or sees other than `expect`
    (for text_in_file, the text must be there)."""
    if probe not in PROBES:
        raise Refusal(f"`{probe}` is not a probe", f"the probes are {', '.join(PROBES)}")
    args = _args(probe, text, key, ref, ancestor_of, casefold)
    try:
        seen = observe(led, probe, target, args)
    except Unreachable as u:
        raise Refusal(f"the probe could not see: {u}", "a fact is recorded only on what its probe sees")
    if probe == "text_in_file" and not seen["present"]:
        raise Refusal(f"{json.dumps(text)} is not in {target}", "exact text, never cut inside a word or a number",
                      "copy the text as the file writes it")
    if expect is not None:
        want = {"file": "sha256", "field": "value", "git_ref": "commit"}.get(probe)
        got = seen.get(want) if want else None
        ok = want and (str(got) == str(expect) or (probe == "git_ref" and str(got).startswith(str(expect))
                                                     and len(str(expect)) >= 7))
        if not ok:
            raise Refusal(f"the probe sees {json.dumps(got)}, not {json.dumps(expect)}",
                          "a fact records what is there, not what was expected", "record what the probe sees")
    body = {"probe": probe, "target": target, "args": args, "seen": seen,
            "statement": statement(probe, target, args, seen)}
    if note.strip():
        body["note"] = {"text": note.strip(), "checked": False}
    return led._append("fact", body)


def recheck(led, fact: dict, rerun: bool = False) -> dict:
    b = fact["body"]
    try:
        now = observe(led, b["probe"], b["target"], b["args"], rerun and b["probe"] == "run")
    except Unreachable as u:
        return {"state": "unreachable", "why": str(u)}
    except Refusal as r:
        return {"state": "unreachable", "why": r.error}
    if b["probe"] == "run" and "holds" not in now:
        return {"state": "as_of", "as_of": now["as_of"]}
    if b["probe"] == "run":
        return {"state": "current" if now["holds"] else "stale", "by": now["run"]}
    if _main(b["probe"], now) == _main(b["probe"], b["seen"]):
        return {"state": "current"}
    return {"state": "stale", "now": now}


# --- decisions --------------------------------------------------------------------------------------------------------
def subjects(entries) -> dict:
    """Every subject key in the ledger, with where it was first used."""
    out = {}
    for e in entries:
        s = e["body"].get("subject") if e["kind"] in ("decision", "plan", "claim") else None
        if s and s not in out:
            out[s] = {"subject": s, "first": e["id"], "at": e["at"], "kind": e["kind"]}
    return out


def _tokens(s: str) -> set:
    return set(re.split(r"[.\-_]", s)) - {""}


def current_decisions(entries) -> dict:
    """The decision standing on each subject."""
    replaced = {e["body"]["supersedes"] for e in entries if e["kind"] == "decision" and e["body"].get("supersedes")}
    out = {}
    for e in entries:
        if e["kind"] == "decision" and e["id"] not in replaced:
            out[e["body"]["subject"]] = e
    return out


def decide(led, subject: str, question: str, choice: str, reason: str, evidence=(), supersedes: str = "",
           new_subject: bool = False) -> dict:
    subject = pl.subject_key(subject)
    if not subject:
        raise Refusal("a decision needs its subject", "a key such as firm.status, so a later decision on it is found")
    for k, v in (("question", question), ("choice", choice), ("reason", reason)):
        if not v.strip():
            raise Refusal(f"a decision needs its {k}")
    entries = led.entries()
    known = subjects(entries)
    if subject not in known and not new_subject:
        like = [s for s in known if _tokens(s) & _tokens(subject)]
        held = {s: current_decisions(entries).get(s) for s in like}
        raise Refusal(f"the ledger has no subject {subject}",
                      ("subjects like it: " + "; ".join(f"{s}" + (f" ({held[s]['body']['question']})" if held[s]
                                                                   else "") for s in like))
                      if like else f"its subjects are {', '.join(sorted(known)) or 'none yet'}",
                      "decide on an existing subject, or pass new_subject=true to create this one")
    standing = current_decisions(entries).get(subject)
    if standing and supersedes != standing["id"]:
        raise Refusal(f"{subject} is decided by {standing['id']}: {standing['body']['choice']}",
                      "a new decision on a subject replaces the one standing, and says so",
                      f"pass supersedes=\"{standing['id']}\" with the reason it changes")
    if supersedes and not standing:
        old = led.get(supersedes, "decision")
        raise Refusal(f"{supersedes} is not the decision standing on {subject}",
                      f"it is on {old['body']['subject']}" if old["body"]["subject"] != subject else
                      "it was superseded already", "supersede the decision that stands")
    ids = []
    for i in evidence or []:
        e = led.get(i)
        if e["kind"] not in EVIDENCE:
            raise Refusal(f"{i} is a {e['kind']}", f"evidence is a {', '.join(EVIDENCE)}")
        ids.append(e["id"])
    from . import critique as cq
    if supersedes:
        cq.gate(entries, supersedes)
    body = {"subject": subject, "question": question.strip(), "choice": choice.strip(), "reason": reason.strip(),
            "evidence": ids, "supersedes": supersedes, **({"new_subject": True} if subject not in known else {})}
    return led._append("decision", body)


def list_decisions(entries, current_only: bool = True, days: int = 14) -> dict:
    cur = current_decisions(entries)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    by_id = {e["id"]: e for e in entries if e["kind"] == "decision"}
    recent = []
    for e in by_id.values():
        if e["body"].get("supersedes") and datetime.fromisoformat(e["at"]) >= since:
            old = by_id.get(e["body"]["supersedes"])
            recent.append({"replaced": _show(old) if old else e["body"]["supersedes"], "by": _show(e)})
    out = {"current": [_show(e) for e in cur.values()], "superseded_recently": recent}
    if not current_only:
        out["all"] = [_show(e) for e in by_id.values()]
    return out


def _show(e: dict) -> dict:
    b = e["body"]
    return {"id": e["id"], "at": e["at"], "subject": b["subject"], "question": b["question"], "choice": b["choice"],
            "reason": b["reason"]}


# --- recall -------------------------------------------------------------------------------------------------------
def _words(s: str) -> set:
    return set(_WORD.findall(s.lower()))


def _text(e: dict) -> str:
    b = e["body"]
    if e["kind"] == "fact":
        return b["statement"]
    if e["kind"] == "decision":
        return " ".join((b["subject"], b["question"], b["choice"], b["reason"]))
    return " ".join((b.get("subject", ""), b["text"]))


def recall(led, query: str, kinds=("fact", "decision", "claim"), rerun: bool = False, limit: int = 20) -> dict:
    words = _words(query)
    if not words:
        raise Refusal("the query has no words", "recall finds entries by the words they share with the query")
    entries = led.entries()
    replaced = {e["body"].get("supersedes") for e in entries if e["kind"] in ("claim", "decision")} - {"", None}
    hits = []
    for e in entries:
        if e["kind"] not in kinds or e["id"] in replaced:
            continue
        mine = _words(_text(e))
        score = len(words & mine) + sum(1 for w in words for m in mine if w != m and w in m) * 0.25
        if score:
            hits.append((score, e["seq"], e))
    hits.sort(key=lambda h: (-h[0], -h[1]))
    out = []
    for _, _, e in hits[:limit]:
        row = {"id": e["id"], "kind": e["kind"], "at": e["at"], "text": _text(e)}
        if e["kind"] == "fact":
            row.update(recheck(led, e, rerun))
            if e["body"].get("note"):
                row["note_unchecked"] = e["body"]["note"]["text"]
        elif e["kind"] == "decision":
            row["state"] = "current"
        out.append(row)
    return {"query": query, "hits": out, "states": {s: sum(1 for r in out if r.get("state") == s)
                                                    for s in ("current", "stale", "unreachable", "as_of")}}


# --- checking -------------------------------------------------------------------------------------------------------
def verify_entry(led, e: dict, seen: dict) -> None:
    b = e["body"]
    earlier = list(seen.values())
    if e["kind"] == "fact":
        if b["probe"] not in PROBES:
            raise Refusal(f"its probe {b['probe']} is not a probe")
        if b["probe"] == "run" and seen.get(b["target"], {}).get("kind") != "citation":
            raise Refusal(f"rests on {b['target']}, which is not an earlier citation")
        if statement(b["probe"], b["target"], b["args"], b["seen"]) != b["statement"]:
            raise Refusal("its statement is not what its probe saw (edited after it was written)")
    elif e["kind"] == "decision":
        if any(seen.get(i) is None for i in b["evidence"]):
            raise Refusal("rests on evidence that is not an earlier entry")
        standing = current_decisions(earlier).get(b["subject"])
        if standing and b.get("supersedes") != standing["id"]:
            raise Refusal(f"decides {b['subject']} while {standing['id']} stands, without superseding it")
        if b.get("supersedes") and not standing:
            raise Refusal(f"supersedes {b['supersedes']}, which was not the decision standing on {b['subject']}")
        if b["subject"] not in subjects(earlier) and not b.get("new_subject"):
            raise Refusal(f"creates the subject {b['subject']} without declaring it new")


def stale_facts(led, entries) -> list:
    out = []
    for e in entries:
        if e["kind"] == "fact":
            r = recheck(led, e)
            if r["state"] in ("stale", "unreachable"):
                out.append({"id": e["id"], "statement": e["body"]["statement"], **r})
    return out


def digest(led, days: int = 14) -> str:
    """Current decisions, recent supersessions and new subjects, and stale facts, as text for a session's start
    or an AGENTS.md block."""
    entries = led.entries()
    d = list_decisions(entries, days=days)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    new = [s for s in subjects(entries).values() if datetime.fromisoformat(s["at"]) >= since]
    lines = []
    if d["current"]:
        lines.append("Current decisions (research-ledger):")
        lines += [f"- {x['subject']}: {x['choice']} ({x['id']}, {x['at'][:10]})" for x in d["current"]]
    if d["superseded_recently"]:
        lines.append(f"Superseded in the last {days} days:")
        lines += [f"- {x['by']['subject']}: was \"{x['replaced']['choice'] if isinstance(x['replaced'], dict) else x['replaced']}\", "
                  f"now \"{x['by']['choice']}\"" for x in d["superseded_recently"]]
    if new:
        lines.append(f"Subjects created in the last {days} days: " + ", ".join(s["subject"] for s in new))
    stale = stale_facts(led, entries)
    if stale:
        lines.append("Facts that no longer hold:")
        lines += [f"- {s['statement']} [{s['state']}]" for s in stale[:20]]
    return "\n".join(lines)


#: printed by the session-start hook in every session, ledger or not: the two places an agent's own habits win
USAGE = ("research-ledger: to remember something a probe can check (a version, a config value, a file, a commit) "
         "for later sessions, use record_fact, not a memory note, and recall it before relying on it. To put "
         "decisions into AGENTS.md or GEMINI.md, use write_digest, not a hand-written copy.")


BEGIN, END = "<!-- research-ledger digest: written by write_digest, edits inside are replaced -->", \
    "<!-- /research-ledger digest -->"


def write_digest(led, path: str, days: int = 14) -> dict:
    """Write the digest into a marked block of a markdown file (AGENTS.md, GEMINI.md), replacing the block if it is
    there and touching nothing outside it."""
    target = guard.check(path)
    if target.suffix.lower() != ".md":
        raise Refusal(f"{path} is not a markdown file", "the digest goes into an agent's instruction file",
                      "give AGENTS.md, GEMINI.md or another .md file")
    text = digest(led, days) or "No decisions or stale facts recorded."
    block = f"{BEGIN}\n{text}\n{END}"
    old = target.read_text(encoding="utf-8") if target.is_file() else ""
    if BEGIN in old and END in old:
        head, rest = old.split(BEGIN, 1)
        new = head + block + rest.split(END, 1)[1]
    else:
        new = old + ("\n\n" if old and not old.endswith("\n\n") else "") + block + "\n"
    target.write_text(new, encoding="utf-8")
    return {"ok": True, "path": str(target), "lines": text.count("\n") + 1}
