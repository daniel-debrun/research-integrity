"""Files the ledger never reads: credentials, keys, other ledgers' internals, and folders of licensed data.

Every read of a file the user names goes through `check`: a fact's probe, a local source, a draft, a plan's holdout,
a report's references. An instrument's snapshot leaves such files out. A path is checked as given (after `~`) and
as resolved (symlinks and `..`), and every component of both must pass, so a dotfile linked from elsewhere is caught
by its name. A refused path is never opened; the refusal names the rule that matched, not the file's contents.

Licensed data (an academic licence that forbids redistribution, say) is listed one folder per line in
`~/.config/research-ledger/licensed_paths`, or the file `$RESEARCH_LEDGER_LICENSED` names. It is kept outside every
project and ledger so that it is never committed. When it is missing or empty, `licensed_status` says so, and the
server reports it at start and in `verify`.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from .quotecheck import Refusal

DIRS = {".ssh", ".gnupg", ".aws", ".kube", ".docker", ".ledger", ".password-store"}
PAIRS = {(".config", "gcloud"), (".git", "config"), (".config", "gh")}
NAMES = (".netrc", "_netrc", ".pgpass", ".pypirc", ".npmrc", ".git-credentials", ".credentials.json", ".env",
         ".env.*", "cookie_save", "*.key", "*.pem", "*.p12", "*.pfx", "*.keystore", "*.kdbx", "id_rsa*",
         "id_dsa*", "id_ecdsa*", "id_ed25519*")
#: a data file named for a secret ("api_token.txt", "secrets.json"), not code ("tokenizer.py", "tokens.py")
SECRET_NAME = re.compile(r"(?i)(^|[._-])(tokens?|secrets?|credentials?|passwords?)([._-]|$)")
DATA_SUFFIXES = {"", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".csv", ".dat", ".xml"}
#: a config key that holds a secret: its value is never recorded
SECRET_KEY = re.compile(r"(?i)(key|token|secret|passw|credential|auth|cookie|session)")


def licensed_file() -> Path:
    return Path(os.environ.get("RESEARCH_LEDGER_LICENSED") or "~/.config/research-ledger/licensed_paths").expanduser()


def licensed() -> list:
    f = licensed_file()
    if not f.is_file():
        return []
    rows = [line.split("#", 1)[0].strip() for line in f.read_text(encoding="utf-8").splitlines()]
    return [Path(r).expanduser().resolve() for r in rows if r]


def licensed_status() -> str:
    """A warning when no licensed-data folder is listed, else ''."""
    if licensed():
        return ""
    return (f"no licensed-data folders are listed in {licensed_file()}: the ledger cannot keep licensed data out "
            "until they are (one folder per line)")


def _rule(parts: tuple) -> str:
    for i, p in enumerate(parts):
        if p in DIRS and i < len(parts) - 1:
            return f"inside {p}/"
        if i + 1 < len(parts) and (p, parts[i + 1]) in PAIRS:
            return f"{p}/{parts[i + 1]}"
    name = parts[-1] if parts else ""
    if name in DIRS:
        return name
    for g in NAMES:
        if fnmatch.fnmatch(name, g):
            return g
    if Path(name).suffix.lower() in DATA_SUFFIXES and SECRET_NAME.search(Path(name).stem or name):
        return "a file named for a secret"
    return ""


def _both(path) -> tuple:
    given = Path(os.path.abspath(Path(path).expanduser()))
    try:
        return given, given.resolve()
    except OSError:
        return given, given


def credential(path) -> str:
    """The rule that marks `path` as a credential or key, or ''. Licensed data is not a credential: the agent may
    read it to work on it, but the ledger never records it."""
    for p in _both(path):
        r = _rule(p.parts)
        if r:
            return r
    return ""


def why(path) -> str:
    """The rule that refuses `path`, or '' when it may be read."""
    given, real = _both(path)
    r = credential(path)
    if r:
        return r
    for folder in licensed():
        if real == folder or folder in real.parents or given == folder or folder in given.parents:
            return f"licensed data ({folder})"
    return ""


def check(path) -> Path:
    """`path`, resolved, or a refusal when the ledger must not read it."""
    r = why(path)
    if r:
        raise Refusal(f"{path} is not read: {r}", "credentials, keys, other ledgers and licensed data never enter "
                      "the ledger", "leave it out; if it is licensed data, nothing derived from it may be recorded")
    return Path(path).expanduser().resolve()


def secret_key(key: str) -> bool:
    return bool(SECRET_KEY.search(key))
