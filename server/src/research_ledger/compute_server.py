"""research-compute served over MCP (stdio): compute nodes, and instruments launched on them, recorded in the same
ledger as research-ledger's ($LEDGER_DIR, else `.ledger/` in the directory the server starts in).

`research-compute --status` prints the launches not yet recorded as runs, reading only the ledger (for a session-start
hook); it prints nothing when there are none.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import compute as cp
from . import plans as pl
from .ledger import Ledger
from .server import _entry, _safe

INSTRUCTIONS = """Compute nodes and instruments launched on them, recorded in the research ledger. Register a node
once with register_node, register an instrument on it with register_instrument_on, and start it with
launch_instrument: the launch is recorded before the job starts, so every launch counts whether or not you collect
it. A launch returns while the job runs; wait_run records its run when it ends, and run_logs and runs_in_flight reach
it meanwhile. Cite a run's output with cite_run (research-ledger)."""


def _ledger(root=None) -> Ledger:
    return Ledger(root or os.environ.get("LEDGER_DIR") or Path.cwd() / ".ledger")


def _run_view(r: dict) -> dict:
    if "run" not in r:
        return r
    e = r["run"]
    b = e["body"]
    return {**{k: v for k, v in r.items() if k != "run"}, "run": e["id"], "exit": b["exit"],
            "timed_out": b["timed_out"], **{k: b[k] for k in ("stopped", "lost", "error") if b.get(k)},
            "outputs": {k: {f: o[f] for f in ("bytes", "kept")} for k, o in b["outputs"].items()}}


def build(root=None) -> MCPServer:
    ledger = _ledger(root)
    server = MCPServer("research-compute", instructions=INSTRUCTIONS, version="0.1.2")

    def reads(title):
        return ToolAnnotations(title=title, read_only_hint=True, open_world_hint=True)

    def appends(title, **kw):
        return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=False, open_world_hint=True, **kw)

    @server.tool(annotations=reads("List compute nodes"))
    def list_nodes() -> dict:
        """The compute nodes (this machine is `local`; others come from the nodes file): whether each is registered,
        its load, free memory and GPU use right now, and its open launches."""
        return _safe(lambda: {"ok": True, "nodes_file": str(cp.inventory_path()), "nodes": cp.list_nodes(ledger)})

    @server.tool(annotations=appends("Register a node", idempotent_hint=True))
    def register_node(name: str) -> dict:
        """Probe a node and record its fingerprint: OS, cores and memory as its limits allow, GPU models, driver
        and CUDA version. Its version is the hash of that; the same fingerprint returns the existing entry."""
        return _safe(lambda: _entry(cp.register_node(ledger, name)))

    @server.tool(annotations=appends("Register an instrument on a node", idempotent_hint=True))
    def register_instrument_on(node: str, name: str, command: str, source: str = "",
                               secrets: list[str] | None = None, outputs: list[str] | None = None,
                               timeout: int = 600) -> dict:
        """Register an instrument as register_instrument does, but fingerprinted on `node` (its program, Python and
        packages there) and bound to the node's registered version. Refused if the node changed since it was
        registered."""
        def go():
            e = cp.register_on(ledger, node, name, command, source, secrets or [], outputs or [], timeout)
            b = e["body"]
            return {"ok": True, "id": e["id"], "ref": f"{b['name']}@{b['version']}", "node": b["node"],
                    "argv": b["argv"], "files": len(b["files"]),
                    "runs_with": [f"{p['kind']}: {p['label']}" for p in b["env"]],
                    **({"already": True} if e.get("already") else {})}
        return _safe(go)

    @server.tool(annotations=appends("Launch an instrument"))
    def launch_instrument(ref: str, args: list[str] | None = None, gpus: int = 0, cores: int = 0,
                          memory_gb: float = 0, minutes: int = 0, follow_seconds: int = 20, plan: str = "",
                          hypothesis: str = "", look: str = "") -> dict:
        """Start a node instrument (name@version) on `args`. The launch is recorded first, so it counts even if
        never collected. Checks that neither the node nor what the instrument runs with has changed, copies the
        code there, and starts it; on an SSH node `gpus` picks free GPUs (CUDA_VISIBLE_DEVICES), on Slurm the
        resources go to sbatch. Follows it for `follow_seconds`; if it ends by then, its run is recorded. `plan`
        and `hypothesis` tag it as a trial (research-ledger's seal_plan); `look` spends a holdout look."""
        return _safe(lambda: _run_view(cp.launch(ledger, ref, args or [], gpus, cores, memory_gb, minutes,
                                                 follow_seconds, pl.tag(ledger, plan, hypothesis, look))))

    @server.tool(annotations=appends("Wait for a launch"))
    def wait_run(launch: str, seconds: int = 60) -> dict:
        """Wait up to `seconds` (at most 600) for a launch. When its job has ended, its outputs are brought back,
        hashed on the node and here, and its run is recorded; otherwise its status and output tail."""
        return _safe(lambda: _run_view(cp.wait(ledger, launch, seconds)))

    @server.tool(annotations=reads("Read a launch's output"))
    def run_logs(launch: str, offset: int = 0, stream: str = "stdout") -> dict:
        """A launch's stdout or stderr so far, from byte `offset` (32 KB at most; `next` is where to read on)."""
        return _safe(lambda: cp.logs(ledger, launch, offset, stream))

    @server.tool(annotations=ToolAnnotations(title="Stop a launch", read_only_hint=False, destructive_hint=True,
                                             open_world_hint=True))
    def stop_run(launch: str) -> dict:
        """Stop a launch's job and record its run as stopped."""
        return _safe(lambda: _run_view(cp.stop(ledger, launch)))

    @server.tool(annotations=reads("Runs in flight"))
    def runs_in_flight() -> dict:
        """Every launch not yet recorded as a run, and whether its job is running, finished (collect it with
        wait_run) or lost."""
        return _safe(lambda: {"ok": True, "launches": cp.in_flight(ledger)})

    return server


def status() -> str:
    """The launches not yet recorded as runs, from the ledger alone; empty when there are none."""
    led = _ledger()
    if not led.chain.exists():
        return ""
    rows = cp.in_flight(led, check=False)
    if not rows:
        return ""
    lines = [f"research-compute: {len(rows)} launch(es) not yet recorded as runs:"]
    lines += [f"- {r['launch']}: {r['ref']} on {r['node']}, launched {r['since']}" for r in rows]
    lines.append("Check them with runs_in_flight, and record each with wait_run.")
    return "\n".join(lines)


def main() -> None:
    if "--status" in sys.argv[1:]:
        out = status()
        if out:
            print(out)
        return
    build().run("stdio")


if __name__ == "__main__":
    main()
