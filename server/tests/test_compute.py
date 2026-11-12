"""Light tests of research-compute: this machine as a node, and an `ssh` stand-in that runs scripts here. No network."""

import subprocess
import sys
import time

import pytest

from research_ledger import compute as cp
from research_ledger import instruments as ins
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal

SSH_SHIM = """#!/bin/sh
# Stands in for ssh: drops the options and the host, and runs the remote command here.
while [ $# -gt 1 ]; do case "$1" in -o|-p) shift 2;; -*) shift;; *) break;; esac; done
shift
exec sh -c "$*"
"""


@pytest.fixture
def led(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_NODES", str(tmp_path / "nodes.toml"))
    return Ledger(tmp_path / ".ledger")


@pytest.fixture
def tool(tmp_path):
    src = tmp_path / "tool"
    src.mkdir()
    (src / "measure.py").write_text(
        "import os, sys, time\n"
        "time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0)\n"
        "print('mean skill 0.71 at 12 stations')\n"
        "open(os.path.join(os.environ['INSTRUMENT_OUT'], 'result.txt'), 'w').write('mean skill 0.71 at 12 stations')\n")
    return src


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "ssh").write_text(SSH_SHIM)
    (bin_ / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{__import__('os').environ['PATH']}")
    (tmp_path / "nodes.toml").write_text(
        f'[[node]]\nname = "box"\nhost = "me@box"\nport = 2222\nworkdir = "{tmp_path / "remote"}"\n')


def on(led, node, tool):
    cp.register_node(led, node)
    return cp.register_on(led, node, "skill", f"{sys.executable} measure.py", str(tool), outputs=["*.txt"])


def test_the_inventory_has_local_and_refuses_an_unknown_key(led, tmp_path):
    assert list(cp.inventory()) == ["local"]
    (tmp_path / "nodes.toml").write_text('[[node]]\nname = "a"\nhost = "h"\nworkdir = "/w"\nflavour = "x"\n')
    with pytest.raises(Refusal, match="unknown key `flavour`"):
        cp.inventory()


def test_a_node_is_registered_by_its_fingerprint(led):
    e = cp.register_node(led, "local")
    assert e["body"]["fingerprint"]["cores"] > 0 and cp.register_node(led, "local")["already"]


def test_a_node_instrument_launches_and_its_run_is_recorded(led, tool):
    inst = on(led, "local", tool)
    assert inst["body"]["node"] == "local" and inst["body"]["env"][-1]["kind"] == "node"
    with pytest.raises(Refusal, match="is registered on the node local"):
        ins.run(led, inst["body"]["name"])
    r = cp.launch(led, inst["body"]["name"], follow=30)
    run = r["run"]["body"]
    assert r["status"] == "recorded" and run["exit"] == 0 and run["node"] == "local"
    assert run["outputs"]["result.txt"]["kept"] and run["outputs"]["result.txt"]["node_sha"]
    c = led.cite_run(r["run"]["id"], "mean skill 0.71 at 12 stations", ["0.71", "12"], file="result.txt")
    led.claim("The instrument measures 0.71 at 12 stations.", [c["id"]])
    v = led.verify()
    assert v["ok"] and v["launch"] == 1 and v["open_launches"] == []


def test_a_launch_counts_before_it_is_collected_and_can_be_stopped(led, tool, monkeypatch):
    name = on(led, "local", tool)["body"]["name"]
    r = cp.launch(led, name, ["30"], follow=0)
    assert r["status"] == "running" and led.verify()["open_launches"] == [r["launch"]]
    assert [x["status"] for x in cp.in_flight(led)] == ["running"]
    monkeypatch.setenv("LEDGER_DIR", str(led.root))
    from research_ledger import compute_server
    assert r["launch"] in compute_server.status()
    s = cp.stop(led, r["launch"])
    assert s["run"]["body"]["stopped"] and cp.in_flight(led) == []
    time.sleep(0.5)
    left = subprocess.run(["pgrep", "-f", r["launch"].split(":")[1]], capture_output=True, text=True).stdout
    assert left == "", f"processes left running after stop: {left}"


def test_a_launch_over_ssh(led, tool, fake_ssh, tmp_path):
    name = on(led, "box", tool)["body"]["name"]
    r = cp.launch(led, name, follow=30)
    assert r["run"]["body"]["node"] == "box" and r["run"]["body"]["exit"] == 0
    assert (tmp_path / "remote" / r["launch"].split(":")[1] / "out" / "result.txt").exists()
    assert "box" in [n["name"] for n in cp.list_nodes(led)]


def test_a_changed_node_refuses_the_launch(led, tool, monkeypatch):
    name = on(led, "local", tool)["body"]["name"]
    real = cp._fingerprint
    monkeypatch.setattr(cp, "_fingerprint", lambda h: {**real(h), "cuda": "99.9"})
    with pytest.raises(Refusal, match="the node local has changed") as e:
        cp.launch(led, name)
    assert "cuda" in e.value.meaning and led.verify()["launch"] == 0


def test_gpus_are_picked_least_used_first_and_held_ones_skipped(led):
    class Box(cp.Host):
        def out(self, script, **kw):
            return "gpu=0, 90, 20000, 24000\ngpu=1, 5, 300, 24000\ngpu=2, 0, 100, 24000\n"
    h = Box({"name": "box", "backend": "ssh"})
    assert cp._pick_gpus(led, h, "box", 2) == ["2", "1"]
    with pytest.raises(Refusal, match="3 GPUs not held by open launches; 4 asked for"):
        cp._pick_gpus(led, h, "box", 4)


def test_a_launch_carries_its_plan_tags_into_its_run(led, tool):
    from research_ledger import plans as pl
    name = on(led, "local", tool)["body"]["name"]
    pl.seal(led, "s", "q?", [{"statement": "it measures", "metric": "skill", "success": "above 0.5"}])
    r = cp.launch(led, name, follow=30, tags=pl.tag(led, "s", "H1"))
    assert r["run"]["body"]["plan"] and r["run"]["body"]["hypothesis"] == "H1"
    assert pl.status(led.entries(), "s")["hypotheses"][0]["trials"] == 1 and led.verify()["ok"]
