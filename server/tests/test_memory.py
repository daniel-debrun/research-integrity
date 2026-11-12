"""Facts re-probed on recall, and decisions on a registry of subjects."""

import json
import subprocess
import sys

import pytest

from research_ledger import instruments as ins
from research_ledger import memory as mem
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


def test_a_file_fact_goes_stale_when_the_file_changes(led, tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("v1\n")
    fact = mem.record_fact(led, "file", str(f))
    assert fact["body"]["statement"].startswith(f"{f} exists with sha256")
    assert mem.recall(led, "notes.md")["hits"][0]["state"] == "current"
    f.write_text("v2\n")
    hit = mem.recall(led, "notes")["hits"][0]
    assert hit["state"] == "stale" and hit["now"]["size"] == 3
    v = led.verify()
    assert v["ok"] and v["stale_facts"][0]["id"] == fact["id"]  # stale is the world changing, not a problem
    f.unlink()
    assert mem.recall(led, "notes")["hits"][0]["state"] == "unreachable"


def test_text_in_file_is_exact_and_never_cut(led, tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("the error is 0.61 on post-processing\n")
    with pytest.raises(Refusal, match="is not in"):
        mem.record_fact(led, "text_in_file", str(f), text="error is 0.6")
    with pytest.raises(Refusal, match="is not in"):
        mem.record_fact(led, "text_in_file", str(f), text="postprocessing")
    with pytest.raises(Refusal, match="too short"):
        mem.record_fact(led, "text_in_file", str(f), text="0.6")
    with pytest.raises(Refusal, match="is not in"):
        mem.record_fact(led, "text_in_file", str(f), text="The Error")
    assert mem.record_fact(led, "text_in_file", str(f), text="The Error", casefold=True)
    assert mem.record_fact(led, "text_in_file", str(f), text="error is 0.61")


def test_a_field_fact_and_a_refused_secret_key(led, tmp_path):
    f = tmp_path / "plugin.json"
    f.write_text(json.dumps({"name": "x", "version": "0.2.0", "auth_token": "abc"}))
    with pytest.raises(Refusal, match="sees \"0.2.0\", not \"0.1.3\""):
        mem.record_fact(led, "field", str(f), expect="0.1.3", key="version")
    fact = mem.record_fact(led, "field", str(f), expect="0.2.0", key="version")
    assert fact["body"]["statement"] == f'{f} has version = "0.2.0"'
    with pytest.raises(Refusal, match="names a secret"):
        mem.record_fact(led, "field", str(f), key="auth_token")
    assert "abc" not in led.chain.read_text()


def test_a_field_key_with_dots_in_brackets(led, tmp_path):
    f = tmp_path / "installed.json"
    f.write_text(json.dumps({"plugins": {"research-integrity@research-integrity": [{"version": "0.2.0"}]}}))
    fact = mem.record_fact(led, "field", str(f), key='plugins["research-integrity@research-integrity"].0.version')
    assert fact["body"]["seen"]["value"] == "0.2.0"


def test_a_credential_file_is_never_probed(led, tmp_path):
    (tmp_path / ".pgpass").write_text("h:1:d:u:p\n")
    with pytest.raises(Refusal, match="could not see"):
        mem.record_fact(led, "file", str(tmp_path / ".pgpass"))


def test_a_git_ref_fact(led, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one")
    sha = git("rev-parse", "HEAD").strip()
    fact = mem.record_fact(led, "git_ref", str(repo), expect=sha[:7], ref="HEAD")
    assert sha in fact["body"]["statement"]
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "two")
    assert mem.recall(led, "HEAD")["hits"][0]["state"] == "stale"


def test_a_run_fact_is_as_of_unless_rerun(led, tmp_path):
    src = tmp_path / "tool"
    src.mkdir()
    (src / "count.py").write_text("print('81 tests passed in 24 seconds')\n")
    ins.register(led, "count", f"{sys.executable} count.py", str(src))
    r = ins.run(led, "count")
    c = led.cite_run(r["id"], "81 tests passed in 24 seconds", ["81"])
    mem.record_fact(led, "run", c["id"])
    assert mem.recall(led, "tests passed")["hits"][0]["state"] == "as_of"
    assert mem.recall(led, "tests passed", rerun=True)["hits"][0]["state"] == "current"


def test_a_note_is_kept_but_marked_unchecked(led, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x\n")
    mem.record_fact(led, "file", str(f), note="this is the final dataset")
    hit = mem.recall(led, "a.txt")["hits"][0]
    assert hit["note_unchecked"] == "this is the final dataset" and "final dataset" not in hit["text"]


def test_an_edited_fact_statement_fails_verify(led, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x\n")
    mem.record_fact(led, "file", str(f))
    from research_ledger.ledger import entry_hash
    e = json.loads(led.chain.read_text())
    e["body"]["statement"] = "the dataset is final"
    e["hash"] = entry_hash(e["seq"], e["kind"], e["at"], e["body"], e["prev"])
    e["id"] = f"fact:{e['hash'][:12]}"
    led.chain.write_text(json.dumps(e) + "\n")
    assert any("not what its probe saw" in p for p in led.verify()["problems"])


def test_decisions_need_a_registered_subject_and_name_what_they_replace(led):
    with pytest.raises(Refusal, match="no subject firm.status"):
        mem.decide(led, "firm.status", "Keep the firm?", "close it", "no paying client")
    d1 = mem.decide(led, "firm.status", "Keep the firm?", "close it", "no paying client", new_subject=True)
    with pytest.raises(Refusal, match="no subject verification-firm.status") as no:
        mem.decide(led, "verification-firm.status", "Is the firm closed?", "yes", "same")
    assert "subjects like it: firm.status (Keep the firm?)" in no.value.meaning
    with pytest.raises(Refusal, match="is decided by"):
        mem.decide(led, "firm.status", "Is the verification firm closed?", "reopen", "a client")
    d2 = mem.decide(led, "firm.status", "Keep the firm?", "reopen", "a client signed", supersedes=d1["id"])
    with pytest.raises(Refusal, match=f"is decided by {d2['id']}"):  # d1 is no longer the one standing
        mem.decide(led, "firm.status", "Keep the firm?", "close", "again", supersedes=d1["id"])
    out = mem.list_decisions(led.entries())
    assert [d["id"] for d in out["current"]] == [d2["id"]]
    assert out["superseded_recently"][0]["replaced"]["id"] == d1["id"]
    text = mem.digest(led)
    assert "firm.status: reopen" in text and "was \"close it\"" in text and "firm.status" in text
    assert mem.recall(led, "firm")["hits"][0]["id"] == d2["id"]
    assert led.verify()["ok"]


def test_a_decision_rests_on_entries_that_exist(led):
    with pytest.raises(Refusal, match="is not an entry"):
        mem.decide(led, "a.b", "q", "c", "r", evidence=["claim:000000000000"], new_subject=True)


def test_the_digest_block_is_replaced_and_nothing_else_touched(led, tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Rules\nBe brief.\n")
    mem.decide(led, "firm.status", "Keep the firm?", "close it", "no client", new_subject=True)
    mem.write_digest(led, str(agents))
    d1 = mem.decide(led, "release.split", "Split?", "0.2.1 then 0.3.0", "bench", new_subject=True)
    mem.write_digest(led, str(agents))
    text = agents.read_text()
    assert text.startswith("# Rules\nBe brief.\n") and text.count(mem.BEGIN) == 1
    assert "release.split: 0.2.1 then 0.3.0" in text
    with pytest.raises(Refusal, match="not a markdown file"):
        mem.write_digest(led, str(tmp_path / "x.txt"))


def test_the_session_start_digest_prints_decisions(led, tmp_path, monkeypatch, capsys):
    from research_ledger import server
    mem.decide(led, "firm.status", "Keep the firm?", "close it", "no client", new_subject=True)
    monkeypatch.setenv("LEDGER_DIR", str(led.root))
    monkeypatch.setattr("sys.argv", ["research-ledger", "--digest"])
    server.main()
    assert "firm.status: close it" in capsys.readouterr().out
