"""research-formal against real Lean, using the skill's worked example; skipped where Lean isn't installed."""

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
from mcp.client.client import Client

from research_ledger import critique as cq
from research_ledger import formal as fm
from research_ledger import formal_server
from research_ledger import instruments as ins
from research_ledger import plans as pl
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal

EXAMPLE = Path(__file__).resolve().parents[2] / "extras/formal/skills/formal-plans/example"
pytestmark = pytest.mark.skipif(not fm._lean_available(), reason="Lean is not installed")

H = [{"statement": "the new loss beats the baseline", "metric": "dev accuracy", "success": "see the formal model"}]
LINKS = {"width_shrinks": "assumed", "baseline_is": "assumed", "enough": "assumed"}
PROOF = """by
  have h := p.width_shrinks
  have hn := p.enough
  by_cases c : w.width_bp ≤ 500
  · exact c
  · exfalso
    have : 501 * 400 ≤ w.width_bp * w.samples := Nat.mul_le_mul (by omega) hn
    omega"""
SCORE = "import sys\nprint(f'accuracy {sys.argv[1]} over {sys.argv[2]} samples on the dev split')\n"


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


@pytest.fixture
def model(tmp_path):
    folder = tmp_path / "model"
    shutil.copytree(EXAMPLE, folder)
    for f in [folder, *folder.rglob("*")]:  # writable even when copied from a read-only snapshot
        f.chmod(f.stat().st_mode | 0o200)
    return folder


@pytest.fixture
def tool(tmp_path, led):
    src = tmp_path / "tool"
    src.mkdir()
    (src / "score.py").write_text(SCORE)
    return ins.register(led, "score", f"{sys.executable} score.py", str(src))["body"]["name"]


def edit(folder, old, new):
    p = folder / "Plan.lean"
    s = p.read_text()
    assert old in s, old
    p.write_text(s.replace(old, new, 1))


def sealed(led):
    return pl.seal(led, "loss-study", "Does the new loss help?", H)


def measure(led, tool, acc, n):
    run = ins.run(led, tool, [acc, n], tags=pl.tag(led, "loss-study", "H1"))
    c = led.cite_run(run["id"], f"accuracy {acc} over {n} samples on the dev split", [acc, n])
    return {"acc": {"value": acc, "citation": c["id"]}, "samples": {"value": n, "citation": c["id"]}}


def test_a_model_is_checked_and_a_passing_result_gets_a_computed_success(led, model, tool):
    sealed(led)
    m = fm.attach(led, "loss-study", "H1", str(model), LINKS)
    assert [p["name"] for p in m["body"]["premises"]] == ["width_shrinks", "baseline_is", "enough"]
    assert m["body"]["fields"] == {"acc": "Rat", "samples": "Nat"} and "Premises w" in m["body"]["design"]
    values = measure(led, tool, "0.60", "500")
    v = fm.conclude(led, "loss-study", "H1", values)
    assert v["body"]["outcome"] == "success" and set(v["body"]["tests"].values()) == {"pass"}
    k = led.claim("The new loss reaches 0.60 over 500 samples.", [values["acc"]["citation"]])
    assert k["body"]["trials"][0]["verdict"] == "success"
    assert pl.status(led.entries(), "loss-study")["hypotheses"][0]["verdict"]["outcome"] == "success"
    assert led.verify()["ok"]


def test_a_failing_result_names_its_part_and_needs_revision(led, model, tool):
    sealed(led)
    fm.attach(led, "loss-study", "H1", str(model), LINKS)
    v = fm.conclude(led, "loss-study", "H1", measure(led, tool, "0.55", "500"))
    assert v["body"]["outcome"] == "revision"
    assert v["body"]["tests"] == {"beats the baseline by the half-width": "fail", "used enough samples": "pass"}
    check = led.verify()
    assert check["ok"] and check["needs_revision"][0]["failed"] == ["beats the baseline by the half-width"]
    pl.amend(led, "loss-study", "the margin was never reachable", question="Does it help at a lower margin?",
             because=[v["id"]])
    assert led.verify()["needs_revision"] == []
    with pytest.raises(Refusal, match="earlier version"):
        fm.conclude(led, "loss-study", "H1", {})


@pytest.mark.parametrize("old, new, why", [
    (PROOF, "by sorry", "axioms:design fail"),
    ("theorem design (w : World) (p : Premises w) : w.width_bp ≤ 500 := " + PROOF,
     "axiom magic : ∀ w : World, w.width_bp ≤ 500\ntheorem design (w : World) (_p : Premises w) : w.width_bp ≤ 500 := "
     "magic w", "axioms:design fail"),
    ("theorem design (w : World) (p : Premises w) : w.width_bp ≤ 500 := " + PROOF, "theorem design : True := trivial",
     "design-rests-on-premises fail"),
    ("fun m => decide (m.acc ≥ (57 : Rat) / 100)", "fun _ => true", "failing-example-fails fail"),
])
def test_a_model_that_proves_nothing_is_refused(led, model, old, new, why):
    sealed(led)
    edit(model, old, new)
    with pytest.raises(Refusal, match="fails its checks") as e:
        fm.attach(led, "loss-study", "H1", str(model), LINKS)
    assert why in e.value.meaning and not [x for x in led.entries() if x["kind"] == "model"]


def test_contradictory_premises_are_refused_however_they_are_dressed(led, model):
    sealed(led)
    edit(model, "  enough : 400 ≤ w.samples", "  enough : 400 ≤ w.samples\n  never : w.samples < 400")
    with pytest.raises(Refusal, match="does not build"):
        fm.attach(led, "loss-study", "H1", str(model), {**LINKS, "never": "assumed"})
    edit(model, "⟨by decide, by decide, by decide⟩", "sorry")
    with pytest.raises(Refusal, match="axioms:model_ok fail"):
        fm.attach(led, "loss-study", "H1", str(model), {**LINKS, "never": "assumed"})


def test_every_premise_has_a_source_and_a_citation_is_shown_beside_it(led, model, tmp_path):
    sealed(led)
    with pytest.raises(Refusal, match="premise `enough` has no match"):
        fm.attach(led, "loss-study", "H1", str(model), {k: v for k, v in LINKS.items() if k != "enough"})
    (tmp_path / "paper.txt").write_text("The baseline accuracy on this task is 0.52 in every published run.\n")
    src = led.add_source(str(tmp_path / "paper.txt"))
    c = led.cite(src["id"], 1, "The baseline accuracy on this task is 0.52", ["0.52"])
    m = fm.attach(led, "loss-study", "H1", str(model), {**LINKS, "baseline_is": c["id"]})
    row = next(p for p in m["body"]["premises"] if p["name"] == "baseline_is")
    assert row["statement"] == "w.baseline_bp = 5200" and row["quote"].startswith("The baseline accuracy")


def test_a_premise_is_recorded_whole_with_its_binders_and_conditions(led, model):
    sealed(led)
    edit(model, "  enough : 400 ≤ w.samples", "  enough : 400 ≤ w.samples\n"
                "  scales : ∀ k : Nat, 0 < k → w.width_bp * k ≤ 500 * k")
    edit(model, "⟨by decide, by decide, by decide⟩", "⟨by decide, by decide, by decide, fun _ _ => Nat.le_refl _⟩")
    m = fm.attach(led, "loss-study", "H1", str(model), {**LINKS, "scales": "assumed"})
    row = next(p for p in m["body"]["premises"] if p["name"] == "scales")
    assert row["statement"].startswith("∀ (k : Nat), 0 < k →") and "w.width_bp * k ≤ 500 * k" in row["statement"]


def test_a_model_comes_before_the_trials_and_values_come_from_them(led, model, tool):
    sealed(led)
    other = ins.run(led, tool, ["0.60", "500"])
    free = led.cite_run(other["id"], "accuracy 0.60 over 500 samples on the dev split")
    fm.attach(led, "loss-study", "H1", str(model), LINKS)
    with pytest.raises(Refusal, match="is not from a run of H1"):
        fm.conclude(led, "loss-study", "H1", {"acc": {"value": "0.60", "citation": free["id"]},
                                              "samples": {"value": "500", "citation": free["id"]}})
    values = measure(led, tool, "0.60", "500")
    with pytest.raises(Refusal, match="0.99 is not written in"):
        fm.conclude(led, "loss-study", "H1", {**values, "acc": {**values["acc"], "value": "0.99"}})
    with pytest.raises(Refusal, match="already has trials"):
        fm.attach(led, "loss-study", "H1", str(model), LINKS)


def test_a_model_can_be_critiqued_and_a_new_one_waits_for_the_answer(led, model, tool, tmp_path):
    sealed(led)
    m = fm.attach(led, "loss-study", "H1", str(model), LINKS)
    cq.critique(led, m["id"], [{"text": "the bound is not the one the source states", "blocking": True}], "adversary")
    with pytest.raises(Refusal, match="is blocking and unanswered"):
        fm.attach(led, "loss-study", "H1", str(model), LINKS)


def test_the_server_exposes_two_annotated_tools(tmp_path):
    async def go():
        async with Client(formal_server.build(tmp_path / ".ledger")) as c:
            return (await c.list_tools()).tools, json.loads(
                (await c.call_tool("conclude_plan", {"plan": "x", "hypothesis": "H1", "values": {}})).content[0].text)
    tools, refused = asyncio.run(go())
    assert {t.name for t in tools} == {"attach_model", "conclude_plan"}
    assert all(t.annotations.destructive_hint is False and t.annotations.title for t in tools)
    assert refused["ok"] is False and "no formal model" in refused["error"]
