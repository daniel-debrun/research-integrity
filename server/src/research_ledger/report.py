"""Checking an agent's hand-back report against the machine, reference by reference.

References are found by regular expressions, never by a model, and each is checked with the same read-only probes
as facts:

- paths, whether or not they exist (`missing` is the case this exists for), with any `:line`;
- commit hashes (7 to 40 hex characters with a letter and a digit, not a ledger id such as `draft:1a2b3c4d5e6f`),
  and the names after the words branch or tag;
- versions next to a name that a version source declares. Wording picks the source: "installed", "enabled" or
  "running" asks what is installed (`~/.claude/plugins/installed_plugins.json`); "declares", "in the repo",
  "bumped" or a manifest's name asks the repository (`plugin.json`, `pyproject.toml`); anything else asks both, and
  two answers that differ are `ambiguous`;
- test counts ("81 passed"), against the latest run whose output a citation quotes with such a count.

States: confirmed, contradicted (with what is there), missing, refused (the guard's), ambiguous, uncheckable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from . import guard
from .quotecheck import Refusal

_PATH = re.compile(r"(?<![\w:/.])((?:~|\.{1,2})?/[\w.@+~-][\w.@+~/-]*|[\w.@+-]+(?:/[\w.@+-]+)+\.[A-Za-z0-9]{1,8}"
                   r"|[\w@+-][\w.@+-]*\.(?:py|md|json|toml|ya?ml|txt|sh|ts|tsx|js|rs|go|c|h|cpp|hpp|tex|csv|ipynb|lean|"
                   r"cfg|ini|lock|pdf))(?::(\d+))?(?![\w/])")
_HEX = re.compile(r"(?<![\w:/.-])([0-9a-f]{7,40})(?![\w-])")
_REF = re.compile(r"\b(branch|tag)\s+[`'\"]?([\w][\w./-]*[\w])[`'\"]?", re.I)
_BARE = re.compile(r"(?<![\w.])v?(\d+\.\d+\.\d+(?:[-+][\w.]+)?)(?![\w.]*\d)")
_COUNT = re.compile(r"\b(\d+)\s+(?:tests?\s+)?passed\b|\b(\d+)\s+tests?\s+pass(?:ed|ing)?\b", re.I)
INSTALLED_WORDS = re.compile(r"\b(installed|enabled|running|loaded)\b", re.I)
REPO_WORDS = re.compile(r"\b(declares?|in the repo|repository|bumped|pyproject|plugin\.json|manifest)\b", re.I)
INSTALLED = Path("~/.claude/plugins/installed_plugins.json")


def _sentence(text: str, i: int) -> str:
    start = max(text.rfind(".", 0, i), text.rfind("\n", 0, i)) + 1
    end = min([j for j in (text.find(". ", i), text.find("\n", i)) if j != -1] or [len(text)])
    return text[start:end]


def _toplevel(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=15)
    except OSError:
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 else None


def installed_versions(path: Path, cwd: Path) -> dict:
    """name -> versions of the install records that apply here (user scope, or a project scope of this folder)."""
    p = path.expanduser()
    if not p.is_file():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for key, records in (data.get("plugins") or {}).items():
        name = key.split("@", 1)[0]
        for r in records if isinstance(records, list) else [records]:
            scope = r.get("scope", "user")
            if scope == "user" or Path(r.get("projectPath", "/nonexistent")).resolve() == cwd.resolve():
                out.setdefault(name, set()).add(str(r.get("version", "")))
    return out


def repo_versions(root: Path | None) -> dict:
    """name -> versions declared by the repository's plugin.json and pyproject.toml files."""
    if root is None:
        return {}
    out = {}
    for f in list(root.rglob("plugin.json"))[:50] + list(root.rglob("pyproject.toml"))[:50]:
        if any(p in (".git", "node_modules", ".venv") for p in f.parts) or guard.why(f):
            continue
        try:
            if f.suffix == ".json":
                d = json.loads(f.read_text(encoding="utf-8"))
            else:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib
                d = tomllib.loads(f.read_text(encoding="utf-8")).get("project", {})
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and d.get("name") and d.get("version"):
            out.setdefault(str(d["name"]), set()).add(str(d["version"]))
    return out


def _path_state(token: str, line: str | None, cwd: Path, root: Path | None) -> dict:
    p = Path(token).expanduser()
    tries = [p] if p.is_absolute() else [cwd / p] + ([root / p] if root else [])
    for t in tries:
        r = guard.why(t)
        if r:
            return {"state": "refused", "why": r}
        if t.exists():
            if line and t.is_file():
                n = sum(1 for _ in open(t, "rb"))
                if int(line) > n:
                    return {"state": "contradicted", "seen": f"{t} has {n} lines"}
            return {"state": "confirmed", "at": str(t)}
    return {"state": "missing"}


def _git_has(root: Path | None, spec: str) -> bool | None:
    if root is None:
        return None
    out = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", spec], capture_output=True,
                         timeout=15)
    return out.returncode == 0


def _latest_count(entries) -> int | None:
    runs = {e["id"]: e for e in entries if e["kind"] == "run"}
    best = None
    for e in entries:
        if e["kind"] == "citation" and e["body"].get("run") in runs:
            m = _COUNT.search(e["body"]["quote"])
            if m:
                best = (runs[e["body"]["run"]]["seq"], int(m.group(1) or m.group(2)))
    return best[1] if best else None


def extract_and_check(text: str, cwd=None, entries=(), installed_path: Path = INSTALLED) -> list:
    cwd = Path(cwd or os.getcwd())
    root = _toplevel(cwd)
    inst, repo = installed_versions(installed_path, cwd), repo_versions(root)
    known = set(inst) | set(repo)
    out, seen = [], set()

    def add(kind, ref, i, **state):
        if (kind, ref) in seen:
            return
        seen.add((kind, ref))
        out.append({"kind": kind, "ref": ref, "line": text.count("\n", 0, i) + 1, **state})

    for m in _PATH.finditer(text):
        tok = m.group(1)
        if text[max(0, m.start() - 3):m.start()].endswith("://") or tok.count(".") == len(tok):
            continue
        if _HEX.fullmatch(tok):
            continue
        add("path", tok + (f":{m.group(2)}" if m.group(2) else ""), m.start(),
            **_path_state(tok, m.group(2), cwd, root))
    for m in _HEX.finditer(text):
        h = m.group(1)
        if not (re.search(r"[a-f]", h) and re.search(r"\d", h)):
            continue
        has = _git_has(root, h + "^{commit}")
        add("commit", h, m.start(), state="uncheckable" if has is None else "confirmed" if has else "missing")
    for m in _REF.finditer(text):
        has = _git_has(root, m.group(2))
        add(m.group(1).lower(), m.group(2), m.start(),
            state="uncheckable" if has is None else "confirmed" if has else "missing")
    last_end = 0
    for m in _BARE.finditer(text):
        ver = m.group(1)
        start = max(text.rfind(".", 0, m.start() - 1) if text[m.start() - 1:m.start()] != "." else -1,
                    text.rfind("\n", 0, m.start()), last_end - 1) + 1
        last_end = m.end()
        before = text[start:m.start()]
        names = [n for n in known if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", before)]
        if not names:
            add("version", ver, m.start(), state="uncheckable", why="no name a version source declares")
            continue
        sent = _sentence(text, m.start())
        for name in sorted(names):
            if INSTALLED_WORDS.search(sent):
                sources = {"installed": inst.get(name)}
            elif REPO_WORDS.search(sent):
                sources = {"repo": repo.get(name)}
            else:
                sources = {"installed": inst.get(name), "repo": repo.get(name)}
            answers = {k: sorted(v) for k, v in sources.items() if v}
            ref = f"{name} {ver}"
            if not answers:
                add("version", ref, m.start(), state="uncheckable")
                continue
            verdicts = {k: ver in v for k, v in answers.items()}
            if len(set(verdicts.values())) > 1 or any(len(v) > 1 for v in answers.values()):
                add("version", ref, m.start(), state="ambiguous", seen=answers)
            elif all(verdicts.values()):
                add("version", ref, m.start(), state="confirmed", seen=answers)
            else:
                add("version", ref, m.start(), state="contradicted", seen=answers)
    latest = _latest_count(list(entries))
    for m in _COUNT.finditer(text):
        n = int(m.group(1) or m.group(2))
        if latest is None:
            add("count", f"{n} passed", m.start(), state="uncheckable")
        else:
            add("count", f"{n} passed", m.start(), state="confirmed" if n == latest else "contradicted",
                seen=f"latest recorded run: {latest} passed")
    return out


STATES = ("confirmed", "contradicted", "missing", "refused", "ambiguous", "uncheckable")


def check_report(led, path_or_text: str, cwd: str = "", record: bool = True) -> dict:
    p = Path(path_or_text).expanduser() if len(path_or_text) < 4096 and "\n" not in path_or_text else None
    if p is not None and p.is_file():
        guard.check(p)
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        text = path_or_text
    if not text.strip():
        raise Refusal("the report is empty")
    refs = extract_and_check(text, cwd or None, led.entries())
    counts = {s: sum(r["state"] == s for r in refs) for s in STATES}
    body = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "counts": counts,
            "flagged": [r for r in refs if r["state"] in ("contradicted", "missing", "ambiguous")]}
    out = {"references": refs, "counts": counts}
    if record:
        e = led._append("report", body)
        out.update({"id": e["id"], "at": e["at"]})
    return out
