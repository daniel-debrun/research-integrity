import sys

import pytest

from research_ledger import critique as cq
from research_ledger import instruments as ins
from research_ledger import plans as pl
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal

SCORE = "import sys\nv = sys.argv[1]\nprint(f'variant {v} scores 0.{v}1 on the dev split')\nsys.exit(int(v == '9'))\n"
H = [{"statement": "the new loss beats the baseline", "metric": "dev accuracy", "success": "above 0.5 on dev"},
     {"statement": "it holds on the holdout", "metric": "holdout accuracy", "success": "above 0.5 on holdout"}]


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


@pytest.fixture
def tool(tmp_path, led):
    src = tmp_path / "tool"
    src.mkdir()
    (src / "score.py").write_text(SCORE)
    return ins.register(led, "score", f"{sys.executable} score.py", str(src))["body"]["name"]


@pytest.fixture
def holdout(tmp_path):
    p = tmp_path / "holdout.csv"
    p.write_text("x,y\n1,0\n2,1\n")
    return p


def trial(led, tool, v, h="H1", look=""):
    return ins.run(led, tool, [v], tags=pl.tag(led, "loss-study", h, look))


def test_a_plan_is_sealed_with_numbered_hypotheses_and_only_once(led, holdout):
    with pytest.raises(Refusal, match="hypothesis 1 has no success"):
        pl.seal(led, "loss-study", "does the loss help?", [{"statement": "it helps", "metric": "acc"}])
    p = pl.seal(led, "loss-study", "Does the new loss help?", H, [str(holdout)])
    assert [h["id"] for h in p["body"]["hypotheses"]] == ["H1", "H2"] and p["body"]["holdout"][0]["sha256"]
    with pytest.raises(Refusal, match="already sealed"):
        pl.seal(led, "loss-study", "again", H)


def test_every_trial_counts_and_a_claim_records_how_many(led, tool):
    pl.seal(led, "loss-study", "Does the new loss help?", H)
    runs = [trial(led, tool, v) for v in ("1", "5", "9")]
    assert [r["body"]["exit"] for r in runs] == [0, 0, 1]
    ins.run(led, tool, ["7"])
    c = led.cite_run(runs[1]["id"], "variant 5 scores 0.51 on the dev split", ["0.51"])
    k = led.claim("The new loss scores 0.51 on dev.", [c["id"]])
    assert k["body"]["trials"] == [{"plan": "loss-study", "hypothesis": "H1", "trials": 3, "reported": 1}]
    st = pl.status(led.entries(), "loss-study")
    h1 = st["hypotheses"][0]
    assert (h1["trials"], h1["succeeded"], h1["failed"], h1["reported"]) == (3, 2, 1, 1)
    assert h1["dropped"] == [runs[0]["id"]] and len(st["untagged_runs"]) == 1
    v = led.verify()
    assert v["ok"] and {"plan": "loss-study", "hypothesis": "H1", "trials": 3, "reported": 1, "dropped": 1} in v["trials"]


def test_a_tag_names_a_real_hypothesis_of_a_sealed_plan(led, tool):
    with pytest.raises(Refusal, match="no plan named"):
        trial(led, tool, "1")
    pl.seal(led, "loss-study", "Does the new loss help?", H)
    with pytest.raises(Refusal, match="`H7` is not a hypothesis"):
        trial(led, tool, "1", "H7")
    with pytest.raises(Refusal, match="needs its plan"):
        pl.tag(led, "", "H1")


def test_an_amendment_needs_a_reason_waits_for_findings_and_keeps_ids(led, tool):
    p = pl.seal(led, "loss-study", "Does the new loss help?", H)
    r = trial(led, tool, "5")
    with pytest.raises(Refusal, match="says why"):
        pl.amend(led, "loss-study", "", hypotheses=H)
    with pytest.raises(Refusal, match="changes nothing"):
        pl.amend(led, "loss-study", "no change", hypotheses=[{**h, "id": f"H{i}"} for i, h in enumerate(H, 1)])
    c = cq.critique(led, p["id"], [{"text": "dev accuracy can be gamed; use a held-out metric", "blocking": True}])
    with pytest.raises(Refusal, match="is blocking and unanswered"):
        pl.amend(led, "loss-study", "use macro F1", hypotheses=H)
    check = led.cite_run(r["id"], "variant 5 scores 0.51 on the dev split")
    cq.answer(led, c["id"], "F1", "upheld", "accuracy hides class imbalance", [check["id"]])
    assert any("still stands" in x for x in led.verify()["problems"])
    new = [{**H[0], "id": "H1", "metric": "dev macro F1"}, {"statement": "it is faster", "metric": "seconds per epoch",
                                                            "success": "under 10"}]
    a = pl.amend(led, "loss-study", "use macro F1", hypotheses=new, because=[f"{c['id']}#F1"])
    assert [h["id"] for h in a["body"]["hypotheses"]] == ["H1", "H3"] and a["body"]["version"] == 2
    assert pl.status(led.entries(), "loss-study")["hypotheses"][0]["trials"] == 1 and led.verify()["ok"]


def test_a_look_is_spent_once_and_refused_on_a_changed_holdout(led, tool, holdout):
    pl.seal(led, "loss-study", "Does the new loss help?", H, [str(holdout)])
    lk = pl.look(led, "loss-study", "the dev result is final")
    trial(led, tool, "9", "H2", lk["id"])
    with pytest.raises(Refusal, match="is spent by"):
        trial(led, tool, "5", "H2", lk["id"])
    assert pl.status(led.entries(), "loss-study")["looks"][0]["used_by"]
    holdout.write_text("x,y\n1,1\n")
    with pytest.raises(Refusal, match="changed since the plan was sealed"):
        pl.look(led, "loss-study", "once more")
    assert led.verify()["ok"]


def test_verify_finds_a_run_tagged_to_a_replaced_version(led, tool):
    old = pl.seal(led, "loss-study", "Does the new loss help?", H)
    pl.amend(led, "loss-study", "narrow the question", question="Does the new loss help on small data?")
    r = trial(led, tool, "5")
    led._append("run", {**r["body"], "plan": old["id"]})
    assert any("an earlier amendment had already replaced" in p for p in led.verify()["problems"])


def test_a_question_is_sealed_once_unless_the_new_plan_continues_the_old(led, tool):
    old = pl.seal(led, "loss-study", "Does the new loss help?", H)
    trial(led, tool, "1")
    trial(led, tool, "5")
    with pytest.raises(Refusal, match="already sealed as loss-study") as r:
        pl.seal(led, "loss-study-v2", "does the new loss help", H)
    assert f'continues="{old["id"]}"' in r.value.fix
    with pytest.raises(Refusal, match="is not a plan of the ledger"):
        pl.seal(led, "loss-study-v2", "Does the new loss help?", H, continues="plan:000000000000")
    new = pl.seal(led, "loss-study-v2", "Does the new loss help?",
                  [{**H[0], "id": "H1"}, {"statement": "it is faster", "metric": "s/epoch", "success": "under 10"}],
                  continues=old["id"])
    assert [h["id"] for h in new["body"]["hypotheses"]] == ["H1", "H3"] and new["body"]["continues"] == old["id"]
    ins.run(led, tool, ["2"], tags=pl.tag(led, "loss-study-v2", "H1"))
    st = pl.status(led.entries(), "loss-study-v2")
    h1, h3 = st["hypotheses"]
    assert (h1["trials"], h1["own"], h1["inherited"]) == (3, 1, 2) and (h3["trials"], h3["inherited"]) == (0, 0)
    assert st["continues"] == ["loss-study"]
    assert pl.status(led.entries(), "loss-study")["hypotheses"][0]["trials"] == 2
    assert led.verify()["ok"]


def test_declared_prior_trials_count_and_show_in_a_claim(led, tool):
    with pytest.raises(Refusal, match="says where the trials were counted"):
        pl.seal(led, "loss-study", "Does the new loss help?", H, prior_trials={"H1": 12})
    with pytest.raises(Refusal, match="names H9, which is not a hypothesis"):
        pl.seal(led, "loss-study", "Does the new loss help?", H, prior_trials={"from": "abc", "H9": 1})
    with pytest.raises(Refusal, match="is not a count"):
        pl.seal(led, "loss-study", "Does the new loss help?", H, prior_trials={"from": "abc", "H1": -1})
    p = pl.seal(led, "loss-study", "Does the new loss help?", H, prior_trials={"from": "ledger head 7f3a…", "H1": 12})
    assert p["body"]["prior_trials"] == {"from": "ledger head 7f3a…", "H1": 12}
    r = trial(led, tool, "5")
    c = led.cite_run(r["id"], "variant 5 scores 0.51 on the dev split", ["0.51"])
    k = led.claim("The new loss scores 0.51 on dev.", [c["id"]])
    assert k["body"]["trials"] == [{"plan": "loss-study", "hypothesis": "H1", "trials": 13, "reported": 1,
                                    "declared": 12}]
    a = pl.amend(led, "loss-study", "narrow it", question="Does the new loss help on small data?")
    assert a["body"]["prior_trials"] == p["body"]["prior_trials"]
    assert pl.status(led.entries(), "loss-study")["hypotheses"][1]["declared"] == 0 and led.verify()["ok"]


def test_verify_finds_a_resealed_question_and_bad_prior_trials(led):
    p = pl.seal(led, "loss-study", "Does the new loss help?", H)
    led._append("plan", {**p["body"], "name": "again"})
    assert any("seals the question of loss-study again under a new name" in x for x in led.verify()["problems"])
    led._append("plan", {**p["body"], "name": "third", "question": "Other?", "prior_trials": {"H1": 3}})
    assert any("prior_trials are not counts" in x for x in led.verify()["problems"])
    led._append("plan", {**p["body"], "name": "fourth", "question": "Another?", "continues": "plan:nope"})
    assert any("continues plan:nope, which is not an earlier plan" in x for x in led.verify()["problems"])


def test_a_reproduction_counts_as_no_trial_and_a_holdout_rerun_needs_a_fresh_look(led, tool, holdout):
    pl.seal(led, "loss-study", "Does the new loss help?", H, [str(holdout)])
    r = trial(led, tool, "5")
    rep = ins.reproduce(led, r["id"])
    st = pl.status(led.entries(), "loss-study")
    assert st["hypotheses"][0]["trials"] == 1 and st["untagged_runs"] == []
    assert "plan" not in rep["run"]["body"] and rep["body"]["verdict"] == "reproduced"
    lk = pl.look(led, "loss-study", "the dev result is final")
    held = trial(led, tool, "9", "H2", lk["id"])
    with pytest.raises(Refusal, match="spent the holdout look"):
        ins.reproduce(led, held["id"])
    with pytest.raises(Refusal, match="is spent by"):
        ins.reproduce(led, held["id"], pl.spend(led, lk["id"], held))
    again = pl.look(led, "loss-study", "reproduce the holdout run")
    rep2 = ins.reproduce(led, held["id"], pl.spend(led, again["id"], held))
    assert rep2["run"]["body"]["look"] == again["id"] and rep2["body"]["verdict"] == "reproduced"
    assert [l["used_by"] for l in pl.status(led.entries(), "loss-study")["looks"]] == [held["id"], rep2["run"]["id"]]
    assert pl.status(led.entries(), "loss-study")["hypotheses"][1]["trials"] == 1 and led.verify()["ok"]
