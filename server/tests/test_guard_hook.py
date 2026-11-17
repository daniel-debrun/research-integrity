import json
import subprocess
import sys
from pathlib import Path

from research_ledger import guard_hook

SCRIPT = Path(guard_hook.__file__)


def run(event: dict) -> str:
    return subprocess.run([sys.executable, "-I", str(SCRIPT)], input=json.dumps(event), capture_output=True,
                          text=True, check=True).stdout


def test_reading_a_credential_file_asks_first(tmp_path):
    out = json.loads(run({"tool_name": "Read", "cwd": str(tmp_path), "tool_input": {"file_path": ".env"}}))
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "ask" and ".env" in h["permissionDecisionReason"]


def test_an_ordinary_file_passes_silently(tmp_path):
    assert run({"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "tokenizer.py")}}) == ""
    assert run({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": str(tmp_path)}}) == ""


def test_a_link_to_a_key_is_caught_by_where_it_points(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519").write_text("not a real key")
    (tmp_path / "notes.txt").symlink_to(tmp_path / ".ssh" / "id_ed25519")
    assert guard_hook.decide({"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "notes.txt")}})


def test_licensed_data_may_be_read_by_the_agent(tmp_path, monkeypatch):
    lic = tmp_path / "licensed"
    lic.write_text(str(tmp_path / "wrds") + "\n")
    monkeypatch.setenv("RESEARCH_LEDGER_LICENSED", str(lic))
    assert guard_hook.decide({"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "wrds" / "crsp.csv")}}) is None


def test_a_glob_for_credential_names_asks_and_bad_input_is_ignored():
    assert guard_hook.decide({"tool_name": "Glob", "tool_input": {"pattern": "**/.env"}})
    assert subprocess.run([sys.executable, "-I", str(SCRIPT)], input="not json", capture_output=True,
                          text=True).stdout == ""
