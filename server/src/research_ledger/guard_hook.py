"""PreToolUse hook: ask before the agent's own file tools open a credential or key file.

The ledger's guard covers only what the ledger reads. An agent asked to record a `.env` will often open it first with
its own Read tool, which puts the secret in the transcript. This hook applies the same name rules (not the licensed-
data list, which the agent may read to work on) to the paths that Read, Edit, Write, MultiEdit, NotebookEdit, Grep and
Glob are given, and asks the user before the call runs. It sees only the path, never the file.

It does not cover Bash (`cat .env`), and a Grep or Glob over a parent folder still reaches files inside it.

Run with the system Python, standard library only, so that it adds no environment start-up to every file read:
    python3 -I guard_hook.py < event.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_ledger import guard  # noqa: E402

FIELDS = ("file_path", "notebook_path", "path")


def decide(event: dict) -> dict | None:
    args = event.get("tool_input") or {}
    for f in FIELDS:
        p = args.get(f)
        if isinstance(p, str) and p:
            if not Path(p).is_absolute() and event.get("cwd"):
                p = str(Path(event["cwd"]) / p)
            r = guard.credential(p)
            if r:
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": "ask",
                    "permissionDecisionReason": f"research-integrity: {args[f]} looks like a credential file ({r}); "
                                                "its contents would enter the transcript"}}
    pattern = args.get("pattern")
    if event.get("tool_name") == "Glob" and isinstance(pattern, str) and guard.credential(Path(pattern).name):
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": f"research-integrity: the pattern {pattern} looks for credential files"}}
    return None


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    out = decide(event)
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    main()
