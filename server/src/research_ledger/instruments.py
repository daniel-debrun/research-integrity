"""Instruments: programs whose output a claim can rest on, recorded by their exact code and environment.

An `instrument` entry holds a command, a snapshot of its code (each file's sha256, the bytes kept in blobs/) and a
fingerprint of what it runs with (the program the command starts, and for Python its version and installed
distributions). Its version is the sha256 of all that, so the same code in the same environment is the same version
and any change is a new one.

A `run` executes a read-only copy of the snapshot, never the live folder, after checking that nothing it runs with
has changed. It records the args, the exit code and the sha256 of its stdout, its stderr and each declared output,
with the declared secrets' values masked, and it is recorded when it fails too. A citation can quote a run's output
the way it quotes a page.

Who may register or run an instrument is the agent's mode's to decide: nothing here asks. An instrument that acts on
the outside world (an order, a message) runs only through its own tool, which is annotated destructive so that hosts
that ask before such actions ask.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .quotecheck import Refusal

SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".tox", ".ledger", "dist", "build", "state"}
SKIP_FILES = (".env", ".env.*", "*.key", "*.pem", "*.pyc", ".DS_Store")
MAX_FILES, MAX_BYTES = 2000, 50 * 2**20          # a snapshot: code, not data or dependencies
KEEP_OUTPUT = 20 * 2**20                          # an output larger than this is hashed, not kept
SHOW_TEXT, TAIL_LINES, TAIL_BYTES = 256 * 2**10, 200, 32 * 2**10
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
SECRET = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SNAP = "{snapshot}"
_LIST_PYTHON = ("import sys, importlib.metadata as m; print(sys.version.split()[0]); "
                "print('\\n'.join(sorted({f\"{d.metadata['Name']}=={d.version}\" for d in m.distributions()})))")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --- the snapshot ---------------------------------------------------------------------------------------------------
def collect(src: Path) -> list:
    """The code files under `src`: git's tracked and unignored files in a work tree, else every file; never
    environments, caches, version control, secrets or build output."""
    try:
        out = subprocess.run(["git", "-C", str(src), "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, timeout=30)
        listed = [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f] if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        listed = None
    if listed is None:
        listed = [str(p.relative_to(src).as_posix()) for p in src.rglob("*") if p.is_file()]
    keep = []
    for rel in sorted(set(listed)):
        parts = rel.split("/")
        if any(d in SKIP_DIRS for d in parts[:-1]) or any(fnmatch.fnmatch(parts[-1], g) for g in SKIP_FILES):
            continue
        if (src / rel).is_file() and not (src / rel).is_symlink():
            keep.append(rel)
    size = sum((src / r).stat().st_size for r in keep)
    if len(keep) > MAX_FILES or size > MAX_BYTES:
        raise Refusal(f"the source is too large to snapshot ({len(keep)} files, {size // 2**20} MB)",
                      f"a snapshot holds the instrument's code, at most {MAX_FILES} files and {MAX_BYTES // 2**20} MB",
                      "point source at the code alone; data and dependencies are not part of it")
    return keep


# --- the environment ------------------------------------------------------------------------------------------------
def _interpreter(script: Path) -> str | None:
    """The program a script's #! line names, if it has one."""
    try:
        first = script.read_bytes()[:256].split(b"\n")[0].decode("utf-8", "replace")
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    words = [w for w in shlex.split(first[2:]) if w != "-S"]
    if not words:
        return None
    return words[1] if Path(words[0]).name == "env" and len(words) > 1 else words[0]


def fingerprint(program: str) -> list:
    """What a program runs as: its resolved path and its file's sha256, and for Python its version and installed
    distributions. Each part has a kind, the program as written, and a sha; a Python listing keeps its text too."""
    path = shutil.which(program)
    if not path:
        raise Refusal(f"`{program}` is not found", "the program a command starts must be installed where the server "
                      "runs", "give its full path, or install it")
    parts = [{"kind": "executable", "label": program, "path": os.path.realpath(path),
              "sha": _sha(Path(path).read_bytes())}]
    if Path(path).name.lower().startswith("python"):
        try:
            out = subprocess.run([path, "-c", _LIST_PYTHON], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Refusal(f"could not list what `{program}` has installed", str(e)[:200])
        parts.append({"kind": "python", "label": program, "text": out.stdout, "sha": _sha(out.stdout.encode())})
    return parts


def _changes(old: dict, new: dict, old_text: str) -> list:
    """What changed between two fingerprints of one part, in words."""
    if old["kind"] == "executable":
        if old.get("path") != new.get("path"):
            return [f"`{old['label']}` now resolves to {new['path']}, not {old['path']}"]
        return [f"`{old['label']}` ({old['path']}) is a different file"]
    before, after = old_text.splitlines(), new["text"].splitlines()
    out = []
    if before[:1] != after[:1]:
        out.append(f"python {before[0] if before else '?'} → {after[0] if after else '?'}")
    was = dict(l.split("==", 1) for l in before[1:] if "==" in l)
    now = dict(l.split("==", 1) for l in after[1:] if "==" in l)
    for name in sorted(set(was) | set(now)):
        if was.get(name) != now.get(name):
            out.append(f"{name} {was.get(name, 'absent')} → {now.get(name, 'absent')}")
    return out or ["the installed distributions changed"]


# --- registering ----------------------------------------------------------------------------------------------------
def register(led, name: str, command: str, source: str = "", secrets=(), outputs=(), timeout: int = 600,
             acts: bool = False) -> dict:
    """Record an instrument version. The same code in the same environment returns the version already there."""
    if not NAME.fullmatch(name or ""):
        raise Refusal(f"`{name}` is not an instrument name", "lowercase letters, digits, '.', '_' and '-'",
                      "pick a short name such as `station-skill`")
    bad = [s for s in secrets if not SECRET.fullmatch(s)]
    if bad:
        raise Refusal(f"`{bad[0]}` is not an environment variable's name",
                      "a secret is given by the variable that holds it, never by its value")
    if not 1 <= int(timeout) <= 86400:
        raise Refusal("the timeout is out of range", "it is in seconds, 1 to 86400")
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise Refusal("the command could not be read", str(e))
    if not argv:
        raise Refusal("the command is empty", "it is the instrument as you would type it")

    files, executable = {}, []
    src = None
    if source:
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise Refusal(f"no folder at {source}", "source is the folder holding the instrument's code")
        for rel in collect(src):
            files[rel] = led._put_blob((src / rel).read_bytes())
            if os.access(src / rel, os.X_OK):
                executable.append(rel)
        tops = {rel.split("/")[0] for rel in files}
        argv = [f"{SNAP}/{a}" if a.split("/")[0] in tops and (src / a).exists() else a for a in argv]

    if argv[0].startswith(SNAP + "/"):
        program = _interpreter(src / argv[0][len(SNAP) + 1:])
        if argv[0][len(SNAP) + 1:] not in executable:
            raise Refusal(f"{argv[0][len(SNAP) + 1:]} is not executable", "a script the command starts directly needs "
                          "its executable bit and a #! line", "chmod +x it, or start it with its interpreter")
    else:
        program = argv[0]
    env = []
    for part in fingerprint(program) if program else []:
        text = part.pop("text", None)
        if text is not None:
            part["sha"] = led._put_blob(text.encode())
        env.append(part)

    manifest = {"name": name, "argv": argv, "files": files, "executable": sorted(executable), "env": env,
                "secrets": sorted(set(secrets)), "outputs": list(outputs), "timeout": int(timeout), "acts": bool(acts)}
    version = _sha(_canonical(manifest))[:12]
    known = next((e for e in led.entries() if e["kind"] == "instrument" and e["body"]["name"] == name
                  and e["body"]["version"] == version), None)
    if known:
        return {**known, "already": True}
    return led._append("instrument", {**manifest, "version": version})


def resolve(led, ref: str) -> dict:
    """An instrument entry from `name@version`, or from a bare name while it has one version."""
    name, _, version = ref.partition("@")
    found = [e for e in led.entries() if e["kind"] == "instrument" and e["body"]["name"] == name
             and (not version or e["body"]["version"] == version)]
    if len(found) == 1:
        return found[0]
    if not found:
        raise Refusal(f"no instrument `{ref}`", "an instrument is named as register_instrument returned it, "
                      "name@version", "list the ledger's instruments")
    raise Refusal(f"`{name}` has {len(found)} versions", ", ".join(f"{name}@{e['body']['version']}" for e in found),
                  "name the version you mean")


def drift(led, inst: dict) -> list:
    """What changed, in words, in what the instrument runs with since it was registered; empty when nothing did."""
    out, programs = [], {}
    for part in inst["body"]["env"]:
        if part["label"] not in programs:
            try:
                programs[part["label"]] = {p["kind"]: p for p in fingerprint(part["label"])}
            except Refusal as r:
                out.append(r.error)
                programs[part["label"]] = {}
                continue
        now = programs[part["label"]].get(part["kind"])
        if now is None:
            continue
        if now["sha"] != part["sha"]:
            old_text = led.blob(part["sha"]).decode("utf-8", "replace") if part["kind"] == "python" else ""
            out += _changes(part, now, old_text)
    return out


# --- running --------------------------------------------------------------------------------------------------------
def _readonly(root: Path) -> None:
    for p in sorted(root.rglob("*"), reverse=True):
        p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def snapshot(led, inst: dict) -> Path:
    """A read-only copy of the instrument's code, built from blobs/ once and checked file by file every run."""
    b = inst["body"]
    if not b["files"]:
        return None
    snap = led.root / "work" / "snapshots" / f"{b['name']}@{b['version']}"
    if not snap.exists():
        tmp = snap.with_name(snap.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        for rel, sha in b["files"].items():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp / rel).write_bytes(led.blob(sha))
            if rel in b["executable"]:
                (tmp / rel).chmod(0o755)
        _readonly(tmp)
        tmp.rename(snap)
    for rel, sha in b["files"].items():
        if not (snap / rel).is_file() or _sha((snap / rel).read_bytes()) != sha:
            raise Refusal(f"the snapshot's copy of {rel} was changed", f"{snap} is rebuilt from the ledger when it "
                          "is removed", f"delete {snap} and run again")
    return snap


def _mask(data: bytes, values: dict) -> bytes:
    for name, value in values.items():
        if len(value) >= 4:
            data = data.replace(value.encode(), f"[secret {name}]".encode())
    return data


def _tail(data: bytes) -> str:
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()[-TAIL_LINES:]
    return "\n".join(lines)[-TAIL_BYTES:]


def run(led, ref: str, args=(), acting: bool = False) -> dict:
    """Run an instrument version on `args` and record the run, failed or not. Refused, with nothing run, when the
    instrument is of the other kind, when what it runs with has changed, or when a secret it needs is not set."""
    inst = resolve(led, ref)
    b = inst["body"]
    label = f"{b['name']}@{b['version']}"
    if b["acts"] and not acting:
        raise Refusal(f"{label} acts on the outside world", "its runs go through run_acting_instrument, which hosts "
                      "that ask before such actions ask about", "call run_acting_instrument")
    if acting and not b["acts"]:
        raise Refusal(f"{label} does not act on the outside world", "it was registered without acts",
                      "call run_instrument")
    if not all(isinstance(a, str) for a in args):
        raise Refusal("every arg is a string", "they are passed to the command as written")
    changed = drift(led, inst)
    if changed:
        raise Refusal(f"what {label} runs with has changed since it was registered", "; ".join(changed),
                      "register it again: that is a new version")
    missing = [s for s in b["secrets"] if not os.environ.get(s)]
    if missing:
        raise Refusal(f"the secret {missing[0]} is not set", "the server passes on only secrets set in its own "
                      "environment", "set it where the server starts (the env of its entry in .mcp.json)")
    values = {s: os.environ[s] for s in b["secrets"]}

    snap = snapshot(led, inst)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(3).hex()
    out = led.root / "work" / "runs" / label / stamp
    out.mkdir(parents=True)
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR", "TEMP")
           if k in os.environ}
    env.update(values, INSTRUMENT=label, INSTRUMENT_OUT=str(out))
    if snap:
        env.update(INSTRUMENT_SNAPSHOT=str(snap))
        if any(p["kind"] == "python" for p in b["env"]):
            env["PYTHONPATH"] = str(snap)
    argv = [a.replace(SNAP, str(snap)) if snap else a for a in b["argv"]] + list(args)

    started = _now()
    try:
        done = subprocess.run(argv, cwd=out, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=b["timeout"])
        code, timed_out, stdout, stderr = done.returncode, False, done.stdout, done.stderr
    except subprocess.TimeoutExpired as e:
        code, timed_out, stdout, stderr = None, True, e.stdout or b"", e.stderr or b""
    except OSError as e:
        raise Refusal(f"{label} could not start", str(e)[:200])
    ended = _now()

    stdout, stderr = _mask(stdout, values), _mask(stderr, values)
    outputs, shown = {}, {}
    for pattern in b["outputs"]:
        for p in sorted(out.glob(pattern)):
            rel = p.relative_to(out).as_posix()
            if not p.is_file() or rel in outputs:
                continue
            data = p.read_bytes()
            try:
                text = _mask(data, values).decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if len(data) > KEEP_OUTPUT:
                outputs[rel] = {"sha": _sha(data), "bytes": len(data), "kept": False}
                continue
            kept = text.encode("utf-8") if text is not None else data
            outputs[rel] = {"sha": led._put_blob(kept), "bytes": len(data), "kept": True, "text": text is not None}
            if text is not None and len(kept) <= SHOW_TEXT:
                shown[rel] = text
    body = {"instrument": inst["id"], "ref": label, "args": list(args), "started": started, "ended": ended,
            "exit": code, "timed_out": timed_out, "stdout": led._put_blob(stdout), "stderr": led._put_blob(stderr),
            "outputs": outputs}
    e = led._append("run", body)
    return {**e, "stdout_tail": _tail(stdout), "stderr_tail": _tail(stderr), "shown": shown}


def output_text(led, run_entry: dict, file: str) -> str:
    """The text a citation of a run quotes: its stdout, its stderr, or one of its kept text outputs."""
    b = run_entry["body"]
    if file in ("stdout", "stderr"):
        return led.blob(b[file]).decode("utf-8", "replace")
    o = b["outputs"].get(file)
    if o is None:
        raise Refusal(f"{file} is not an output of {run_entry['id']}",
                      f"its outputs are stdout, stderr{''.join(', ' + k for k in b['outputs'])}")
    if not o["kept"] or not o.get("text"):
        raise Refusal(f"{file} cannot be quoted", "it is too large to keep" if not o["kept"] else "it is not text",
                      "write the figure the claim needs to a small text output, and register that version")
    return led.blob(o["sha"]).decode("utf-8")


# --- checking -------------------------------------------------------------------------------------------------------
def verify_entry(led, e: dict, seen: dict) -> None:
    """Refuse when an instrument's version no longer matches its manifest or its code, or a run's instrument or
    stored output is not what was recorded."""
    b = e["body"]
    if e["kind"] == "instrument":
        manifest = {k: v for k, v in b.items() if k != "version"}
        if _sha(_canonical(manifest))[:12] != b["version"]:
            raise Refusal("its version does not match what it holds")
        for rel, sha in b["files"].items():
            if _sha(led.blob(sha)) != sha:
                raise Refusal(f"its code file {rel} was changed")
    elif e["kind"] == "run":
        if seen.get(b["instrument"], {}).get("kind") != "instrument":
            raise Refusal(f"runs {b['instrument']}, which is not an earlier instrument")
        kept = [b["stdout"], b["stderr"]] + [o["sha"] for o in b["outputs"].values() if o["kept"]]
        if any(_sha(led.blob(sha)) != sha for sha in kept):
            raise Refusal("an output it recorded was changed")
