from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_skill_script_is_the_servers_check():
    """The citation-check skill ships the same file the server imports, so a quote passes or fails the same way
    with or without the server. Copy server/src/research_ledger/quotecheck.py over the skill's after a change."""
    canonical = ROOT / "server/src/research_ledger/quotecheck.py"
    assert (ROOT / "skills/citation-check/scripts/quotecheck.py").read_bytes() == canonical.read_bytes()
