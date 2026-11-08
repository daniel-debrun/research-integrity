"""research-formal served over MCP (stdio): formal models of a plan's hypotheses and their computed verdicts, recorded
in the same ledger as research-ledger's ($LEDGER_DIR, else `.ledger/` in the directory the server starts in)."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import formal as fm
from .ledger import Ledger
from .server import _entry, _safe

INSTRUCTIONS = """A plan's understanding as a machine-checked model. After seal_plan and before any run, write the
hypothesis as a Lean model (the formal-plans skill gives the contract) and attach it with attach_model. After its runs,
conclude_plan computes the verdict from cited values: success, or needed revision with the part that failed. On
revision, amend the plan citing the verdict; don't argue with it."""


def build(root=None) -> MCPServer:
    ledger = Ledger(root or os.environ.get("LEDGER_DIR") or Path.cwd() / ".ledger")
    server = MCPServer("research-formal", instructions=INSTRUCTIONS, version="0.1.3")

    @server.tool(annotations=ToolAnnotations(title="Attach a formal model", read_only_hint=False,
                                             destructive_hint=False, idempotent_hint=False, open_world_hint=True))
    def attach_model(plan: str, hypothesis: str, folder: str, premises: dict[str, str]) -> dict:
        """Check a Lean model of a plan's hypothesis and record it, before any trial under the plan's current
        version. `folder` holds Plan.lean and lean-toolchain. `premises` maps every field of `Premises` to a citation
        id, or to "assumed". Refused if it doesn't build, anything rests on sorry or an added axiom, `design` doesn't
        take the premises, or the success condition doesn't pass its passing example and fail its failing one.
        Returns each premise beside its quote, for review: code can't check that they mean the same thing."""
        return _safe(lambda: _entry(fm.attach(ledger, plan, hypothesis, folder, premises)))

    @server.tool(annotations=ToolAnnotations(title="Compute a verdict", read_only_hint=False, destructive_hint=False,
                                             idempotent_hint=False, open_world_hint=True))
    def conclude_plan(plan: str, hypothesis: str, values: dict[str, dict]) -> dict:
        """Compute a hypothesis's verdict with Lean. `values` maps each measured field to {"value": ..., "citation":
        id}, where the citation (from cite_run) quotes one of the hypothesis's own runs and writes the value. The
        outcome is success, or revision with the tests that failed. On revision, amend the plan citing this verdict."""
        return _safe(lambda: _entry(fm.conclude(ledger, plan, hypothesis, values)))

    return server


def main() -> None:
    build().run("stdio")


if __name__ == "__main__":
    main()
