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
