"""The hand-back checker, with the golden case from a real session: a fork said the installed plugins were still on
0.1.3 and all three needed updating, while research-integrity was installed at 0.2.0."""

import json
import subprocess

import pytest

from research_ledger import report as rp
from research_ledger.ledger import Ledger


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    for d, name in (("", "research-integrity"), ("extras/compute", "research-compute"),
                    ("extras/formal", "research-formal")):
        (repo / d / ".claude-plugin").mkdir(parents=True)
        (repo / d / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": "0.2.0"}))
    (repo / "server").mkdir()
    (repo / "server" / "ledger.py").write_text("a\nb\nc\n")
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one")
    git("tag", "v0.2.0")
    installed = tmp_path / "installed_plugins.json"
    installed.write_text(json.dumps({"version": 2, "plugins": {
        "research-integrity@research-integrity": [{"scope": "user", "version": "0.2.0"}],
        "research-compute@research-integrity": [{"scope": "user", "version": "0.1.3"}],
        "research-formal@research-integrity": [{"scope": "user", "version": "0.1.3"}]}}))
    return {"repo": repo, "installed": installed, "sha": git("rev-parse", "HEAD").strip()}


def check(world, text):
    return {r["ref"]: r for r in rp.extract_and_check(text, world["repo"], installed_path=world["installed"])}


def test_golden_installed_wording_checks_installed_state(world):
    out = check(world, "research-integrity, research-compute and research-formal are installed at 0.1.3.")
    assert out["research-integrity 0.1.3"]["state"] == "contradicted"
    assert out["research-compute 0.1.3"]["state"] == "confirmed"
    assert out["research-formal 0.1.3"]["state"] == "confirmed"


def test_golden_without_installed_asks_both_sources(world):
    out = check(world, "research-integrity, research-compute and research-formal are at 0.1.3.")
    assert out["research-integrity 0.1.3"]["state"] == "contradicted"  # both say 0.2.0
    assert out["research-compute 0.1.3"]["state"] == "ambiguous"       # installed 0.1.3, repo 0.2.0
    assert out["research-formal 0.1.3"]["state"] == "ambiguous"


def test_golden_unnamed_wording_is_uncheckable(world):
    out = check(world, "The installed plugins are still on 0.1.3; all three need updating.")
    assert out["0.1.3"]["state"] == "uncheckable"


def test_paths_commits_and_tags(world):
    text = (f"I edited server/ledger.py:2 and server/ledger.py:9, added server/new_module.py, and committed "
            f"{world['sha'][:7]} on tag v0.2.0. The ledger id draft:1a2b3c4d5e6f is not a commit; nor is 1234567. "
            f"See https://example.com/a/b.py and branch feature-x.")
    out = check(world, text)
    assert out["server/ledger.py:2"]["state"] == "confirmed"
    assert out["server/ledger.py:9"]["state"] == "contradicted"
    assert out["server/new_module.py"]["state"] == "missing"
    assert out[world["sha"][:7]]["state"] == "confirmed"
    assert out["v0.2.0"]["state"] == "confirmed" and out["feature-x"]["state"] == "missing"
    assert "1a2b3c4d5e6f" not in out and "1234567" not in out
    assert not any("example.com" in k for k in out)


def test_a_credential_path_is_refused_not_opened(world, tmp_path):
    out = check(world, f"The key is in {tmp_path}/.ssh/id_ed25519 and {tmp_path}/.pgpass")
    assert {r["state"] for r in out.values()} == {"refused"}


def test_counts_against_the_latest_recorded_run(world, tmp_path):
    import sys
    from research_ledger import instruments as ins
    led = Ledger(tmp_path / ".ledger")
    src = tmp_path / "t"
    src.mkdir()
    (src / "t.py").write_text("print('===== 81 passed in 24.1s =====')\n")
    ins.register(led, "suite", f"{sys.executable} t.py", str(src))
    r = ins.run(led, "suite")
    led.cite_run(r["id"], "===== 81 passed in 24.1s =====", ["81"])
    res = rp.check_report(led, "All 83 tests pass.", str(world["repo"]))
    assert res["references"][0]["state"] == "contradicted" and res["counts"]["contradicted"] == 1
    assert led.verify()["report"] == 1
