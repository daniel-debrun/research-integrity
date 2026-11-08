"""research-formal: a plan's understanding stated as a machine-checked model, and its result as a computed verdict.

A model is a Lean file, `Plan.lean`, with one namespace per hypothesis (`H1`, ...) declaring:

- `World`: the quantities the plan reasons about, and `Premises w`: what must be true of them, one field per premise.
  Each premise is linked to a citation in the ledger, or marked assumed.
- `model` and `model_ok : Premises model`: a world where the premises hold, so they don't contradict each other.
- `design`: a theorem from `Premises w` to what the plan relies on, such as why a parameter has its value.
- `Measured`: what a run reports. `checks`: the success condition, part by part, as named `Measured -> Bool` tests.
- `passing` and `failing`: an example that meets the condition and one that doesn't, so it can fail.

`attach_model` checks all that in a recorded run, before any trial of the hypothesis: it builds, nothing rests on
`sorry` or an added axiom, the shapes are right, every premise has its source. `conclude_plan` takes the measured
values from citations of the hypothesis's own runs, and Lean computes the verdict in a second recorded run: success,
or needed revision with the part that failed.

The proofs are checked by Lean's kernel. The verdict is computed by Lean's evaluator (`#eval`), because the kernel
does not reduce `Rat` comparisons. What no check can establish is that a Lean statement means what its source means;
`attach_model` returns each premise beside its quote for that review, and a model can be critiqued like a draft.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from . import critique as cq
from . import instruments as ins
from . import plans as pl
from . import quotecheck as qc
from .quotecheck import Refusal

RUN_SH = """#!/bin/sh
# Runs in a run's own folder: copies the registered model, builds it, and runs the check or the verdict.
set -e
export PATH="$HOME/.elan/bin:$PATH"
cp -r "$INSTRUMENT_SNAPSHOT" work
chmod -R u+w work
cd work
if [ -f lakefile.toml ] || [ -f lakefile.lean ]; then
  if [ -f packages.path ]; then mkdir -p .lake; ln -s "$(cat packages.path)" .lake/packages; fi
  lake build >&2
  LEAN="lake env lean"
else
  lean -o Plan.olean Plan.lean >&2
  export LEAN_PATH=.
  LEAN=lean
fi
case "$1" in
  check) $LEAN Check.lean ;;
  verdict)
    { echo "import Plan"; echo "def measured : $2.Measured := $3"; cat Verdict.lean.in; } > Verdict.lean
    $LEAN Verdict.lean ;;
esac
"""

CHECK = """import Lean
import Plan
open Lean Meta

def NS : Name := `@NS@
def line (s : String) : IO Unit := IO.println s
def oneLine (s : String) : String := " ".intercalate (s.splitOn "\\n" |>.map String.trimAscii |>.map toString)

/-- A structure field's full statement: only the structure's own arguments are opened, never the field's binders. -/
def fieldType (struct proj : Name) : MetaM String := do
  let arity := (← getConstInfo struct).type.getForallBinderNames.length
  let s ← forallBoundedTelescope (← getConstInfo proj).type (some (arity + 1)) fun _ b => ppExpr b
  return oneLine (toString s)

#eval show MetaM Unit from do
  let env ← getEnv
  let allowed := [``propext, ``Classical.choice, ``Quot.sound]
  for n in [`model_ok, `design, `checks] do
    let axs ← collectAxioms (NS ++ n)
    let bad := axs.toList.filter (!allowed.contains ·)
    line s!"CHECK axioms:{n} {if bad.isEmpty then "ok" else "fail"} {bad}"
  let ty := (← getConstInfo (NS ++ `design)).type
  let shaped ← forallTelescope ty fun xs _ => do
    if h : 1 < xs.size then
      let t ← inferType xs[1]
      return t.getAppFn.constName? == some (NS ++ `Premises)
    else return false
  line s!"CHECK design-rests-on-premises {if shaped then "ok" else "fail"}"
  line s!"DESIGN {oneLine (toString (← ppExpr ty))}"
  for f in getStructureFields env (NS ++ `Premises) do
    if let some proj := getProjFnForField? env (NS ++ `Premises) f then
      line s!"PREMISE {f} :: {← fieldType (NS ++ `Premises) proj}"
  for f in getStructureFields env (NS ++ `Measured) do
    if let some proj := getProjFnForField? env (NS ++ `Measured) f then
      line s!"FIELD {f} :: {← fieldType (NS ++ `Measured) proj}"

example : @NS@.Premises @NS@.model := @NS@.model_ok
#eval line s!"CHECK passing-example-passes {if @NS@.checks.all (fun c => c.2 @NS@.passing) then "ok" else "fail"}"
#eval line s!"CHECK failing-example-fails {if @NS@.checks.all (fun c => c.2 @NS@.failing) then "fail" else "ok"}"
#eval do for c in @NS@.checks do line s!"TEST {c.1}"
"""

VERDICT = """#eval do for c in @NS@.checks do IO.println s!"CHECK {c.1} :: {if c.2 measured then "pass" else "fail"}"
#eval IO.println s!"VERDICT {if @NS@.checks.all (fun c => c.2 measured) then "success" else "revision"}"
"""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-") or "plan"


def _lean_available() -> bool:
    return bool(shutil.which("lean") or (Path.home() / ".elan" / "bin" / "lean").exists())


def _stage(folder: Path, ns: str) -> Path:
    """A copy of the model with its checker, outside any git work tree, for the instrument's snapshot."""
    stage = Path(tempfile.mkdtemp(prefix="research-formal-"))
    dest = stage / "model"
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns(".lake", ".git", "build", "*.olean", "*.ilean"))
    (dest / "run.sh").write_text(RUN_SH)
    (dest / "Check.lean").write_text(CHECK.replace("@NS@", ns))
    (dest / "Verdict.lean.in").write_text(VERDICT.replace("@NS@", ns))
    packages = folder / ".lake" / "packages"
    if packages.is_dir():
        (dest / "packages.path").write_text(str(packages.resolve()))
    return dest


def _models(entries, plan: str, hypothesis: str) -> list:
    return [e for e in entries if e["kind"] == "model" and e["body"]["name"] == plan
            and e["body"]["hypothesis"] == hypothesis]


def _report(led, run: dict) -> dict:
    """What a check run printed."""
    text = led.blob(run["body"]["stdout"]).decode("utf-8", "replace")
    out = {"checks": [], "premises": {}, "fields": {}, "tests": [], "design": ""}
    for line in text.splitlines():
        if line.startswith("CHECK "):
            out["checks"].append(line[6:])
        elif line.startswith("PREMISE "):
            k, _, v = line[8:].partition(" :: ")
            out["premises"][k] = v
        elif line.startswith("FIELD "):
            k, _, v = line[6:].partition(" :: ")
            out["fields"][k] = v
        elif line.startswith("TEST "):
            out["tests"].append(line[5:])
        elif line.startswith("DESIGN "):
            out["design"] = line[7:]
    return out


def attach(led, plan: str, hypothesis: str, folder: str, premises: dict) -> dict:
    """Check a formal model and record it for a plan's hypothesis, before any trial under the plan's current version.
    `premises` maps each premise field to a citation id, or to "assumed"."""
    entries = led.entries()
    p = pl.current(entries, plan)
    if p is None:
        raise Refusal(f"no plan named {plan}", "seal it first with seal_plan")
    if hypothesis not in [h["id"] for h in p["body"]["hypotheses"]]:
        raise Refusal(f"`{hypothesis}` is not a hypothesis of {plan}")
    done = [e["id"] for e in entries if e["kind"] in ("run", "launch") and e["body"].get("plan") == p["id"]
            and e["body"].get("hypothesis") == hypothesis]
    if done:
        raise Refusal(f"{hypothesis} already has trials under this version of {plan}", "a model states the plan's "
                      "understanding before its runs, not after them", "amend the plan, then attach the model")
    prior = _models(entries, plan, hypothesis)
    if prior:
        cq.gate(entries, prior[-1]["id"])
    src = Path(folder).expanduser().resolve()
    if not (src / "Plan.lean").is_file() or not (src / "lean-toolchain").is_file():
        raise Refusal(f"{folder} is not a model", "a model folder holds Plan.lean and lean-toolchain",
                      "pin the toolchain, for example `leanprover/lean4:v4.35.0`")
    if not _lean_available():
        raise Refusal("Lean is not installed where the server runs", "models are checked by Lean itself",
                      "install elan from https://lean-lang.org/install")
    stage = _stage(src, hypothesis)
    try:
        inst = ins.register(led, f"formal-{_slug(plan)}-{hypothesis.lower()}", "sh run.sh", str(stage), timeout=3600)
    finally:
        shutil.rmtree(stage.parent, ignore_errors=True)
    ref = f"{inst['body']['name']}@{inst['body']['version']}"
    run = ins.run(led, ref, ["check", hypothesis])
    if run["body"]["exit"] != 0:
        raise Refusal("the model does not build", run["stderr_tail"][-600:] or run["stdout_tail"][-600:],
                      "fix it: a required declaration may be missing or a proof may not check")
    rep = _report(led, run)
    failed = [c for c in rep["checks"] if " fail" in c]
    if failed or not rep["checks"]:
        raise Refusal(f"the model fails its checks: {failed[0] if failed else 'no output'}", "; ".join(failed)[:500],
                      "nothing may rest on sorry or an added axiom, design must take Premises, and the success "
                      "condition must pass its passing example and fail its failing one")
    extra = sorted(set(premises) - set(rep["premises"]))
    missing = sorted(set(rep["premises"]) - set(premises))
    if missing or extra:
        raise Refusal(f"premise `{(missing or extra)[0]}` has no match", f"the model's premises are "
                      f"{', '.join(rep['premises'])}", "give each premise a citation id, or \"assumed\"")
    linked = []
    for name, statement in rep["premises"].items():
        source = premises[name]
        quote = ""
        if source != "assumed":
            quote = led.get(source, "citation")["body"]["quote"]
        linked.append({"name": name, "statement": statement, "source": source, "quote": quote})
    body = {"name": plan, "plan": p["id"], "hypothesis": hypothesis, "instrument": inst["id"], "ref": ref,
            "check_run": run["id"], "premises": linked, "design": rep["design"], "fields": rep["fields"],
            "tests": rep["tests"], "supersedes": prior[-1]["id"] if prior else ""}
    return led._append("model", body)


def _literal(fields: dict, values: dict) -> str:
    parts = []
    for name, kind in fields.items():
        d = Decimal(str(values[name]))
        if kind in ("Nat", "Int"):
            if d != d.to_integral_value() or (kind == "Nat" and d < 0):
                raise Refusal(f"{name} is a {kind}, and {d} isn't")
            parts.append(f"{name} := ({int(d)} : {kind})")
        elif kind == "Rat":
            f = Fraction(d)
            parts.append(f"{name} := (({f.numerator} : Int) : Rat) / ({f.denominator} : Rat)")
        else:
            raise Refusal(f"{name} has type {kind}", "measured fields are Nat, Int or Rat")
    return "{ " + ", ".join(parts) + " }"


def conclude(led, plan: str, hypothesis: str, values: dict) -> dict:
    """Compute a hypothesis's verdict. `values` maps each measured field to {"value", "citation"}: the citation must
    quote the output of one of the hypothesis's own runs, and write the value."""
    entries = led.entries()
    models = _models(entries, plan, hypothesis)
    p = pl.current(entries, plan)
    if not models or p is None:
        raise Refusal(f"{hypothesis} of {plan} has no formal model", "a verdict is computed from a model",
                      "attach one with attach_model before the runs")
    m = models[-1]
    if m["body"]["plan"] != p["id"]:
        raise Refusal(f"the model was checked against an earlier version of {plan}", "the plan was amended since",
                      "attach a model for the current version")
    fields = m["body"]["fields"]
    missing = sorted(set(fields) - set(values))
    if missing:
        raise Refusal(f"no value for `{missing[0]}`", f"the model measures {', '.join(fields)}")
    ids = {v["id"] for v in pl.versions(entries, plan)}
    runs = {e["id"]: e for e in entries if e["kind"] == "run"}
    plain = {}
    used = {}
    for name in fields:
        v = values[name]
        if not isinstance(v, dict) or "value" not in v or "citation" not in v:
            raise Refusal(f"`{name}` needs a value and its citation", 'give {"value": ..., "citation": "citation:..."}',
                          "cite the run output that writes it with cite_run")
        c = led.get(v["citation"], "citation")
        r = runs.get(c["body"].get("run"))
        if not r or r["body"].get("plan") not in ids or r["body"].get("hypothesis") != hypothesis:
            raise Refusal(f"{v['citation']} is not from a run of {hypothesis}", "a verdict rests on the hypothesis's "
                          "own trials", "cite the output of a run tagged to it")
        if qc.as_number(v["value"]) not in qc.numbers(c["body"]["quote"]):
            raise Refusal(f"{v['value']} is not written in {v['citation']}", f"the quote writes "
                          f"{', '.join(sorted(map(str, qc.numbers(c['body']['quote'])))) or 'no number'}")
        plain[name] = v["value"]
        used[name] = {"value": str(v["value"]), "citation": c["id"]}
    run = ins.run(led, m["body"]["ref"], ["verdict", hypothesis, _literal(fields, plain)])
    text = led.blob(run["body"]["stdout"]).decode("utf-8", "replace")
    found = re.search(r"^VERDICT (success|revision)$", text, re.M)
    if run["body"]["exit"] != 0 or not found:
        raise Refusal("the verdict could not be computed", run["stderr_tail"][-500:])
    tests = dict(re.findall(r"^CHECK (.+) :: (pass|fail)$", text, re.M))
    body = {"name": plan, "plan": p["id"], "hypothesis": hypothesis, "model": m["id"], "run": run["id"],
            "values": used, "tests": tests, "outcome": found.group(1)}
    return led._append("verdict", body)


# --- reading and checking -------------------------------------------------------------------------------------------
def latest_verdict(entries, plan: str, hypothesis: str):
    return next((e for e in reversed(entries) if e["kind"] == "verdict" and e["body"]["name"] == plan
                 and e["body"]["hypothesis"] == hypothesis), None)


def needs_revision(entries) -> list:
    """Hypotheses whose latest verdict was revision, on the plan's current version."""
    out = []
    for name in sorted({e["body"]["name"] for e in entries if e["kind"] == "verdict"}):
        cur = pl.current(entries, name)
        for h in cur["body"]["hypotheses"]:
            v = latest_verdict(entries, name, h["id"])
            if v and v["body"]["outcome"] == "revision" and v["body"]["plan"] == cur["id"]:
                out.append({"plan": name, "hypothesis": h["id"], "verdict": v["id"],
                            "failed": [k for k, r in v["body"]["tests"].items() if r == "fail"]})
    return out


def verify_entry(led, e: dict, seen: dict) -> None:
    b = e["body"]
    if e["kind"] == "model":
        if seen.get(b["plan"], {}).get("kind") != "plan":
            raise Refusal(f"models {b['plan']}, which is not an earlier plan")
        run = seen.get(b["check_run"])
        if not run or run["kind"] != "run" or run["body"]["instrument"] != b["instrument"] or run["body"]["exit"] != 0:
            raise Refusal(f"rests on {b['check_run']}, which is not a passing check of its instrument")
        if any(" fail" in c for c in _report(led, run)["checks"]):
            raise Refusal("its check run recorded a failure")
        for pr in b["premises"]:
            if pr["source"] != "assumed" and seen.get(pr["source"], {}).get("kind") != "citation":
                raise Refusal(f"premise {pr['name']} cites {pr['source']}, which is not an earlier citation")
        if b["supersedes"] and seen.get(b["supersedes"], {}).get("kind") != "model":
            raise Refusal(f"supersedes {b['supersedes']}, which is not an earlier model")
    elif e["kind"] == "verdict":
        if seen.get(b["model"], {}).get("kind") != "model":
            raise Refusal(f"rests on {b['model']}, which is not an earlier model")
        run = seen.get(b["run"])
        if not run or run["kind"] != "run":
            raise Refusal(f"rests on {b['run']}, which is not an earlier run")
        text = led.blob(run["body"]["stdout"]).decode("utf-8", "replace")
        if f"VERDICT {b['outcome']}" not in text:
            raise Refusal("its outcome is not what its run printed")
        for name, v in b["values"].items():
            c = seen.get(v["citation"])
            if not c or c["kind"] != "citation":
                raise Refusal(f"{name} cites {v['citation']}, which is not an earlier citation")
            if qc.as_number(v["value"]) not in qc.numbers(c["body"]["quote"]):
                raise Refusal(f"{v['value']} is not written in {v['citation']}")
