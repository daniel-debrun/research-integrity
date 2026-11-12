"""The guard: credentials, keys, other ledgers and licensed data are never read, through any tool."""

import sys

import pytest

from research_ledger import guard
from research_ledger import instruments as ins
from research_ledger import plans as pl
from research_ledger.critique import record_draft
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal

REFUSED = [".netrc", ".pgpass", ".pypirc", ".npmrc", ".git-credentials", ".claude/.credentials.json", ".env",
           ".env.local", "cookie_save", "server.key", "cert.pem", "id_rsa", "id_ed25519.pub", ".ssh/config",
           ".aws/credentials", ".kube/config", ".docker/config.json", ".config/gcloud/creds.db", "repo/.git/config",
           "api_token.txt", "secrets.json", "db_password", "other/.ledger/chain.jsonl"]
ALLOWED = ["paper.txt", "tokenizer/vocab.py", "tokens.py", "src/secret_sharing.py", "notes.md", ".gitignore"]


@pytest.mark.parametrize("rel", REFUSED)
def test_credential_paths_are_refused(tmp_path, rel):
    assert guard.why(tmp_path / rel)
    with pytest.raises(Refusal, match="is not read"):
        guard.check(tmp_path / rel)


@pytest.mark.parametrize("rel", ALLOWED)
def test_ordinary_paths_are_allowed(tmp_path, rel):
    assert guard.why(tmp_path / rel) == ""


def test_a_symlinked_dotfile_is_refused_by_its_own_name(tmp_path):
    (tmp_path / "dotfiles").mkdir()
    (tmp_path / "dotfiles" / "netrc").write_text("machine x login y password z\n")
    (tmp_path / ".netrc").symlink_to(tmp_path / "dotfiles" / "netrc")
    assert guard.why(tmp_path / ".netrc") == ".netrc"


def test_a_link_into_a_credential_folder_is_refused(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "known").write_text("x\n")
    (tmp_path / "harmless.txt").symlink_to(tmp_path / ".ssh" / "known")
    assert guard.why(tmp_path / "harmless.txt")
    assert guard.why(tmp_path / "sub" / ".." / ".ssh" / "known")


def test_licensed_folders_are_refused_and_an_empty_list_is_reported(tmp_path, monkeypatch):
    assert "no licensed-data folders" in guard.licensed_status()
    (tmp_path / "wrds").mkdir()
    (tmp_path / "wrds" / "crsp.csv").write_text("permno,ret\n")
    (tmp_path / "licensed_paths").write_text(f"{tmp_path / 'wrds'}  # academic licence\n")
    assert guard.licensed_status() == ""
    assert guard.why(tmp_path / "wrds" / "crsp.csv").startswith("licensed data")


def test_every_tool_that_reads_a_file_uses_the_guard(tmp_path):
    led = Ledger(tmp_path / ".ledger")
    (tmp_path / ".netrc").write_text("machine x\n")
    with pytest.raises(Refusal, match="is not read"):
        led.add_source(str(tmp_path / ".netrc"))
    with pytest.raises(Refusal, match="is not read"):
        record_draft(led, str(tmp_path / ".netrc"))
    h = [{"statement": "s", "metric": "m", "success": "x"}]
    with pytest.raises(Refusal, match="is not read"):
        pl.seal(led, "p", "q?", h, holdout=[str(tmp_path / ".netrc")])
    assert led.entries() == []


def test_a_snapshot_leaves_credentials_out(tmp_path):
    led = Ledger(tmp_path / ".ledger")
    src = tmp_path / "tool"
    src.mkdir()
    (src / "score.py").write_text("print('ok')\n")
    (src / ".pgpass").write_text("host:5432:db:user:pw\n")
    (src / "api_token.txt").write_text("abc\n")
    inst = ins.register(led, "score", f"{sys.executable} score.py", str(src))
    assert set(inst["body"]["files"]) == {"score.py"}
