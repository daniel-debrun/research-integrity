import sys
import textwrap

import pytest

from research_ledger import instruments as ins
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal

MEASURE = textwrap.dedent('''
    import json, os, sys
    print("token " + os.environ.get("STATION_TOKEN", ""))
    print("mean skill 0.71 at 12 stations")
    with open(os.path.join(os.environ["INSTRUMENT_OUT"], "result.json"), "w") as f:
        json.dump({"mean_skill": 0.71, "stations": 12}, f)
    sys.exit(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
''')


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


@pytest.fixture
def tool(tmp_path):
    src = tmp_path / "tool"
    (src / ".venv").mkdir(parents=True)
    (src / ".venv" / "site.py").write_text("")
    (src / ".env").write_text("STATION_TOKEN=from-a-file")
    (src / "measure.py").write_text(MEASURE)
    return src


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("STATION_TOKEN", "s3cr3t-value")
    return "s3cr3t-value"


def register(led, tool, **kw):
    return ins.register(led, "station-skill", f"{sys.executable} measure.py", str(tool),
                        secrets=["STATION_TOKEN"], outputs=["*.json"], **kw)


def program(tmp_path, body, name="probe"):
    """A program outside any source folder, as a system command would be."""
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


def test_a_version_is_its_code_and_environment(led, tool):
    e = register(led, tool)
    b = e["body"]
    assert set(b["files"]) == {"measure.py"} and b["argv"] == [sys.executable, "{snapshot}/measure.py"]
    assert [p["kind"] for p in b["env"]] == ["executable", "python"] and b["secrets"] == ["STATION_TOKEN"]
    assert register(led, tool)["already"]
    (tool / "measure.py").write_text(MEASURE + "\n# changed\n")
    assert register(led, tool)["body"]["version"] != b["version"]
    with pytest.raises(Refusal, match="has 2 versions"):
        ins.resolve(led, "station-skill")


def test_a_run_executes_the_registered_code_and_masks_secrets(led, tool, secret):
    v = register(led, tool)["body"]["version"]
    (tool / "measure.py").write_text(MEASURE.replace("0.71", "0.99"))
    r = ins.run(led, f"station-skill@{v}")
    assert r["body"]["exit"] == 0 and "mean skill 0.71" in r["stdout_tail"]
    assert "token [secret STATION_TOKEN]" in r["stdout_tail"] and secret.encode() not in led.blob(r["body"]["stdout"])
    assert r["shown"] == {"result.json": '{"mean_skill": 0.71, "stations": 12}'}
    assert (led.root / ".gitignore").read_text() == ".lock\nwork/\n"


def test_a_failed_run_is_recorded_and_a_missing_secret_runs_nothing(led, tool, secret, monkeypatch):
    v = register(led, tool)["body"]["version"]
    assert ins.run(led, f"station-skill@{v}", ["3"])["body"]["exit"] == 3
    monkeypatch.delenv("STATION_TOKEN")
    with pytest.raises(Refusal, match="STATION_TOKEN is not set"):
        ins.run(led, f"station-skill@{v}")
    assert [e["kind"] for e in led.entries()] == ["instrument", "run"]


def test_a_changed_program_refuses_the_run(led, tmp_path):
    path = program(tmp_path, 'echo "reading 0.42 units"')
    e = ins.register(led, "probe", str(path))
    assert ins.run(led, "probe")["body"]["exit"] == 0
    path.write_text("#!/bin/sh\necho 'reading 0.40 units'\n")
    with pytest.raises(Refusal, match="has changed since it was registered") as r:
        ins.run(led, "probe")
    assert "is a different file" in r.value.meaning and r.value.fix == "register it again: that is a new version"
    assert sum(x["kind"] == "run" for x in led.entries()) == 1 and e["body"]["files"] == {}


def test_a_python_change_is_named_package_by_package():
    old = {"kind": "python", "label": "python"}
    assert ins._changes(old, {"text": "3.12.1\nnumpy==2.0.1\nscipy==1.13.0\n"}, "3.12.1\nnumpy==1.26.4\npandas==2.2.2\n") \
        == ["numpy 1.26.4 → 2.0.1", "pandas 2.2.2 → absent", "scipy absent → 1.13.0"]


def test_a_timed_out_run_is_recorded(led, tmp_path):
    ins.register(led, "slow", str(program(tmp_path, "echo started; sleep 3")), timeout=1)
    b = ins.run(led, "slow")["body"]
    assert b["timed_out"] and b["exit"] is None


def test_an_instrument_that_acts_runs_only_through_its_own_tool(led, tmp_path):
    ins.register(led, "order", str(program(tmp_path, "echo placed", "order")), acts=True)
    ins.register(led, "read", str(program(tmp_path, "echo read", "read")))
    with pytest.raises(Refusal, match="acts on the outside world"):
        ins.run(led, "order")
    with pytest.raises(Refusal, match="does not act"):
        ins.run(led, "read", acting=True)
    assert ins.run(led, "order", acting=True)["body"]["exit"] == 0


def test_a_claim_rests_on_a_run_and_verify_checks_its_stored_output(led, tool, secret):
    run = ins.run(led, register(led, tool)["body"]["name"])
    c = led.cite_run(run["id"], "mean skill 0.71 at 12 stations", ["0.71", 12])
    j = led.cite_run(run["id"], '{"mean_skill": 0.71, "stations": 12}', ["0.71"], file="result.json")
    assert led.claim("The instrument measures a mean skill of 0.71 at 12 stations.", [c["id"], j["id"]])
    with pytest.raises(Refusal, match="is not on page 1"):
        led.cite_run(run["id"], "mean skill 0.75 at 12 stations")
    with pytest.raises(Refusal, match="is not an output"):
        led.cite_run(run["id"], '{"mean_skill": 0.71, "stations": 12}', file="other.json")
    assert led.verify()["ok"] and led.verify()["run"] == 1

    blob = led.blobs / run["body"]["stdout"]
    blob.write_bytes(blob.read_bytes().replace(b"0.71", b"0.75"))
    problems = led.verify()["problems"]
    assert any("an output it recorded was changed" in p for p in problems)
    assert any("citation" in p and "not on page 1" in p for p in problems)


def test_verify_finds_changed_instrument_code(led, tool):
    sha = register(led, tool)["body"]["files"]["measure.py"]
    (led.blobs / sha).write_text("print('mean skill 0.99')")
    assert any("its code file measure.py was changed" in p for p in led.verify()["problems"])


def test_a_snapshot_skips_environments_secrets_and_caches(tool):
    (tool / "__pycache__").mkdir()
    (tool / "__pycache__" / "m.pyc").write_bytes(b"x")
    (tool / "deploy.key").write_text("k")
    assert ins.collect(tool) == ["measure.py"]


def test_the_work_folder_can_be_deleted_but_snapshot_files_cannot_be_edited(led, tool, secret):
    import shutil
    r = ins.run(led, register(led, tool)["body"]["name"])
    snap = led.root / "work" / "snapshots" / r["body"]["ref"]
    with pytest.raises(PermissionError):
        (snap / "measure.py").write_text("print('edited')")
    shutil.rmtree(led.root / "work")
    assert not (led.root / "work").exists()


def test_a_deterministic_run_reproduces_and_a_timed_one_differs_only_where_it_should(led, tool, secret, tmp_path):
    run = ins.run(led, register(led, tool)["body"]["name"])
    rep = ins.reproduce(led, run["id"])
    b = rep["body"]
    assert (b["verdict"], b["exit"], b["stdout"], b["outputs"]) == ("reproduced", True, True, {"result.json": True})
    assert rep["run"]["body"]["reproduces"] == run["id"] and rep["differences"] == {}
    # a program whose stdout carries the time, but whose declared output is fixed
    path = program(tmp_path, 'date +%N; echo "reading 0.42" > out.txt')
    ins.register(led, "timed", str(path), outputs=["*.txt"])
    r2 = ins.run(led, "timed")
    rep2 = ins.reproduce(led, r2["id"])
    assert rep2["body"]["verdict"] == "reproduced" and rep2["body"]["stdout"] is False
    assert rep2["body"]["outputs"] == {"out.txt": True} and "stdout" in rep2["differences"]
    # the same program with no declared output: stdout decides
    ins.register(led, "clock", str(program(tmp_path, "date +%N", "clock")))
    rep3 = ins.reproduce(led, ins.run(led, "clock")["id"])
    assert rep3["body"]["verdict"] == "differs" and rep3["differences"]["stdout"].startswith("line 1:")
    v = led.verify()
    assert v["ok"] and v["reproduction"] == 3 and v["reproductions"] == {"reproduced": 2, "differs": [rep3["id"]]}


def test_a_reproduction_is_refused_for_acting_runs_reproductions_and_drift(led, tmp_path, monkeypatch):
    ins.register(led, "order", str(program(tmp_path, "echo placed", "order")), acts=True)
    placed = ins.run(led, "order", acting=True)
    with pytest.raises(Refusal, match="acts on the outside world"):
        ins.reproduce(led, placed["id"])
    ins.register(led, "read", str(program(tmp_path, "echo read", "read")))
    rep = ins.reproduce(led, ins.run(led, "read")["id"])
    with pytest.raises(Refusal, match="is itself a reproduction"):
        ins.reproduce(led, rep["body"]["by"])
    monkeypatch.setattr(ins, "drift", lambda led, inst, where=ins.LOCAL: ["`read` is a different file"])
    with pytest.raises(Refusal, match="has changed since it was registered"):
        ins.reproduce(led, rep["body"]["of"])


def test_verify_finds_a_doctored_reproduction(led, tmp_path):
    ins.register(led, "clock", str(program(tmp_path, "date +%N", "clock")))
    rep = ins.reproduce(led, ins.run(led, "clock")["id"])
    assert rep["body"]["verdict"] == "differs"
    chain = led.chain.read_text().replace('"verdict":"differs"', '"verdict":"reproduced"')
    led.chain.write_text(chain)
    problems = led.verify()["problems"]
    assert any("does not match its hash" in p for p in problems)
    led._append("reproduction", {**rep["body"], "verdict": "reproduced"})
    assert any("its comparison is not what the two runs' records give" in p for p in led.verify()["problems"])
