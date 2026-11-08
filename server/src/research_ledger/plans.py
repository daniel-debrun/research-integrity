"""Plans: what a study will test and how it will judge it, sealed before the runs it governs; every run counted.

A `plan` entry names a question and its hypotheses, each with what is measured and what counts as a result, and the
holdout files (by sha256) that the work must not look at until it decides to. A plan is changed only by an amendment,
a new version that supersedes the last, with its reason; like a draft's revision, it is refused while a blocking
finding on the version it replaces is unanswered.

A run or launch is tagged to a plan's hypothesis when it starts, never afterwards, so the record shows every trial
behind a hypothesis: the ones that succeeded, failed or are still running, the ones some claim reports, and the
successful ones nothing reports. A claim resting on a tagged run records that count when it is made ("rests on 1 of
40 trials"), and `verify` recomputes it. Runs of the same instruments made without a tag after the plan was sealed
are listed too, as trials the plan may not be counting.

A `look` spends one view of the holdout: it is recorded with its reason, refused if a holdout file changed since the
seal, and at most one run uses it. Looks are counted, not limited. Nothing stops a program from reading a holdout file
directly on this machine; the record shows which runs said they did.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .quotecheck import Refusal

TAGS = ("plan", "hypothesis", "look")


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def versions(entries, name: str) -> list:
    return [e for e in entries if e["kind"] == "plan" and e["body"]["name"] == name]


def current(entries, name: str):
    v = versions(entries, name)
    return v[-1] if v else None


def _hypotheses(items, old=()) -> list:
    """Check and number hypotheses: each states what it claims, what is measured and what counts as a result.
    On an amendment, a hypothesis that keeps an old id keeps its trials."""
    if not items:
        raise Refusal("a plan has at least one hypothesis", "each says what it claims, what is measured and what "
                      "counts as a result")
    known = {h["id"] for h in old}
    taken = set(known)
    out = []
    for n, h in enumerate(items, 1):
        if not isinstance(h, dict):
            raise Refusal(f"hypothesis {n} is not an object", 'give {"statement", "metric", "success"}')
        missing = [k for k in ("statement", "metric", "success") if not str(h.get(k, "")).strip()]
        if missing:
            raise Refusal(f"hypothesis {n} has no {missing[0]}", "a hypothesis says what it claims (statement), what "
                          "is measured (metric) and what counts as a result (success), before any run")
        hid = str(h.get("id", "")).strip()
        if hid and hid not in known:
            raise Refusal(f"{hid} is not a hypothesis of the plan", "an amendment keeps an id only for a hypothesis "
                          "the plan already has", "leave id out for a new hypothesis")
        if not hid:
            i = 1
            while f"H{i}" in taken:
                i += 1
            hid = f"H{i}"
        if any(x["id"] == hid for x in out):
            raise Refusal(f"{hid} appears twice", "each hypothesis once")
        taken.add(hid)
        out.append({"id": hid, **{k: str(h[k]).strip() for k in ("statement", "metric", "success")},
                    "comparison": str(h.get("comparison", "")).strip()})
    return out


def _holdout(paths) -> list:
    out = []
    for p in paths or []:
        path = Path(p).expanduser().resolve()
        if not path.is_file():
            raise Refusal(f"no file at {p}", "a holdout is a file the work must not see until a look")
        out.append({"path": str(path), "sha256": _sha_file(path)})
    return out


def seal(led, name: str, question: str, hypotheses: list, holdout=()) -> dict:
    """Record a plan before the runs it governs."""
    if not name.strip() or not question.strip():
        raise Refusal("a plan needs a name and a question")
    if current(led.entries(), name):
        raise Refusal(f"a plan named {name} is already sealed", "a plan changes only by amendment, with its reason",
                      "amend it with amend_plan")
    return led._append("plan", {"name": name, "question": question.strip(), "hypotheses": _hypotheses(hypotheses),
                                "holdout": _holdout(holdout), "version": 1, "supersedes": "", "reason": "",
                                "because": []})


def amend(led, name: str, reason: str, question: str = "", hypotheses=None, holdout=None, because=()) -> dict:
    """Record a plan's next version, with the reason for the change and what prompted it (finding refs, runs,
    citations). Refused while a blocking finding on the current version is unanswered."""
    from . import critique as cq
    entries = led.entries()
    old = current(entries, name)
    if old is None:
        raise Refusal(f"no plan named {name}", "seal it first with seal_plan")
    if not reason.strip():
        raise Refusal("an amendment says why", "the reason the plan changes, recorded with it")
    cq.gate(entries, old["id"])
    for ref in because or []:
        led.get(ref.split("#", 1)[0])
    b = old["body"]
    body = {"name": name, "question": question.strip() or b["question"],
            "hypotheses": _hypotheses(hypotheses, b["hypotheses"]) if hypotheses is not None else b["hypotheses"],
            "holdout": _holdout(holdout) if holdout is not None else b["holdout"], "version": b["version"] + 1,
            "supersedes": old["id"], "reason": reason.strip(), "because": list(because or [])}
    if {k: body[k] for k in ("question", "hypotheses", "holdout")} == {k: b[k] for k in ("question", "hypotheses",
                                                                                         "holdout")}:
        raise Refusal("the amendment changes nothing", "an amendment changes the question, a hypothesis or the holdout")
    return led._append("plan", body)


def look(led, plan: str, reason: str) -> dict:
    """Spend one look at a plan's holdout. Refused if a holdout file is missing or changed since the seal."""
    p = current(led.entries(), plan)
    if p is None:
        raise Refusal(f"no plan named {plan}")
    if not p["body"]["holdout"]:
        raise Refusal(f"the plan {plan} has no holdout", "a holdout is named when the plan is sealed or amended")
    if not reason.strip():
        raise Refusal("a look says why now", "a look is spent once; its reason is recorded with it")
    for h in p["body"]["holdout"]:
        path = Path(h["path"])
        if not path.is_file() or _sha_file(path) != h["sha256"]:
            raise Refusal(f"the holdout file {h['path']} is missing or changed since the plan was sealed",
                          "a look is at the holdout as sealed", "restore it, or amend the plan with its reason")
    return led._append("look", {"plan": p["id"], "name": plan, "reason": reason.strip(),
                                "holdout": [h["sha256"] for h in p["body"]["holdout"]]})


def tag(led, plan: str = "", hypothesis: str = "", look_id: str = "") -> dict:
    """The tags a run or launch carries: the plan's current version, one of its hypotheses, and a look to spend."""
    if not plan:
        if hypothesis or look_id:
            raise Refusal("a hypothesis or look needs its plan", "give plan as well")
        return {}
    entries = led.entries()
    p = current(entries, plan)
    if p is None:
        raise Refusal(f"no plan named {plan}", "seal it first with seal_plan")
    ids = [h["id"] for h in p["body"]["hypotheses"]]
    if hypothesis not in ids:
        raise Refusal(f"`{hypothesis}` is not a hypothesis of {plan}", f"its hypotheses are {', '.join(ids)}",
                      "name the hypothesis this run tests")
    out = {"plan": p["id"], "hypothesis": hypothesis}
    if look_id:
        lk = led.get(look_id, "look")
        if lk["body"]["name"] != plan:
            raise Refusal(f"{look_id} is a look at {lk['body']['name']}, not {plan}")
        used = next((e for e in entries if e["kind"] in ("run", "launch") and e["body"].get("look") == look_id), None)
        if used:
            raise Refusal(f"{look_id} is spent by {used['id']}", "one run per look; a look whose run fails stays spent",
                          "take another look with look_holdout, and its reason")
        out["look"] = look_id
    return out


# --- counting -------------------------------------------------------------------------------------------------------
def _standing_claims(entries) -> list:
    superseded = {e["body"].get("supersedes") for e in entries if e["kind"] == "claim"} - {"", None}
    return [e for e in entries if e["kind"] == "claim" and e["id"] not in superseded]


def _reported_runs(entries) -> set:
    """Runs some standing claim rests on, through a citation of their output."""
    cites = {e["id"]: e for e in entries if e["kind"] == "citation"}
    return {cites[c]["body"].get("run") for cl in _standing_claims(entries) for c in cl["body"]["citations"]
            if c in cites} - {None}


def _ok(run: dict) -> bool:
    b = run["body"]
    return b["exit"] == 0 and not b["timed_out"] and not any(b.get(k) for k in ("stopped", "lost", "error"))


def counts(entries, name: str, hypothesis: str) -> dict:
    """Trials of one hypothesis: every tagged run and every launch not yet recorded as a run."""
    ids = {v["id"] for v in versions(entries, name)}
    runs = [e for e in entries if e["kind"] == "run" and e["body"].get("plan") in ids
            and e["body"].get("hypothesis") == hypothesis]
    ran = {e["body"].get("launch") for e in entries if e["kind"] == "run"}
    open_ = [e for e in entries if e["kind"] == "launch" and e["body"].get("plan") in ids
             and e["body"].get("hypothesis") == hypothesis and e["id"] not in ran]
    reported = _reported_runs(entries)
    ok = [r for r in runs if _ok(r)]
    return {"trials": len(runs) + len(open_), "succeeded": len(ok), "failed": len(runs) - len(ok),
            "running": len(open_), "reported": sum(r["id"] in reported for r in runs),
            "dropped": [r["id"] for r in ok if r["id"] not in reported], "runs": [r["id"] for r in runs]}


def status(entries, name: str) -> dict:
    p = current(entries, name)
    if p is None:
        raise Refusal(f"no plan named {name}")
    ids = {v["id"] for v in versions(entries, name)}
    first = versions(entries, name)[0]["seq"]
    def formal(hid):
        model = next((e["id"] for e in reversed(entries) if e["kind"] == "model" and e["body"]["name"] == name
                      and e["body"]["hypothesis"] == hid), None)
        verdict = next((e for e in reversed(entries) if e["kind"] == "verdict" and e["body"]["name"] == name
                        and e["body"]["hypothesis"] == hid), None)
        return {**({"model": model} if model else {}),
                **({"verdict": {"id": verdict["id"], "outcome": verdict["body"]["outcome"],
                                "tests": verdict["body"]["tests"]}} if verdict else {})}

    hyps = [{"id": h["id"], "statement": h["statement"], **counts(entries, name, h["id"]), **formal(h["id"])}
            for h in p["body"]["hypotheses"]]
    tagged = [e for e in entries if e["kind"] == "run" and e["body"].get("plan") in ids]
    used = {e["body"]["instrument"] for e in tagged}
    untagged = [e["id"] for e in entries if e["kind"] == "run" and e["seq"] > first and not e["body"].get("plan")
                and e["body"]["instrument"] in used]
    looks = [{"look": e["id"], "reason": e["body"]["reason"],
              "used_by": next((r["id"] for r in entries if r["kind"] in ("run", "launch")
                               and r["body"].get("look") == e["id"]), None)}
             for e in entries if e["kind"] == "look" and e["body"]["plan"] in ids]
    return {"plan": name, "id": p["id"], "version": p["body"]["version"], "question": p["body"]["question"],
            "amendments": [{"id": v["id"], "version": v["body"]["version"], "reason": v["body"]["reason"],
                            "because": v["body"]["because"]} for v in versions(entries, name)[1:]],
            "hypotheses": hyps, "untagged_runs": untagged, "looks": looks}


def claim_trials(entries, citations: list) -> list:
    """For a claim about to rest on `citations` (entries): each plan hypothesis its runs test, with how many trials
    stand behind it and how many runs claims report, this one included."""
    plans = {e["id"]: e for e in entries if e["kind"] == "plan"}
    runs = {e["id"]: e for e in entries if e["kind"] == "run"}
    seen, out = set(), []
    mine = {c["body"].get("run") for c in citations} - {None}
    for rid in sorted(mine):
        r = runs.get(rid)
        if not r or not r["body"].get("plan"):
            continue
        name, h = plans[r["body"]["plan"]]["body"]["name"], r["body"]["hypothesis"]
        if (name, h) in seen:
            continue
        seen.add((name, h))
        c = counts(entries, name, h)
        reported = len(set(c["runs"]) & (_reported_runs(entries) | mine))
        verdict = next((e["body"]["outcome"] for e in reversed(entries) if e["kind"] == "verdict"
                        and e["body"]["name"] == name and e["body"]["hypothesis"] == h), None)
        out.append({"plan": name, "hypothesis": h, "trials": c["trials"], "reported": reported,
                    **({"verdict": verdict} if verdict else {})})
    return out


# --- checking -------------------------------------------------------------------------------------------------------
def verify_entry(led, e: dict, seen: dict) -> None:
    from . import critique as cq
    b = e["body"]
    earlier = list(seen.values())
    if e["kind"] == "plan":
        ids = [h["id"] for h in b["hypotheses"]]
        if not ids or len(set(ids)) != len(ids):
            raise Refusal("its hypotheses are missing or repeat an id")
        prev = current(earlier, b["name"])
        if b["supersedes"]:
            if not prev or prev["id"] != b["supersedes"]:
                raise Refusal(f"supersedes {b['supersedes']}, which is not the plan's latest earlier version")
            if cq.unanswered_blocking(earlier, b["supersedes"]):
                raise Refusal("it was recorded while a blocking finding on the version it replaces was unanswered")
        elif prev:
            raise Refusal(f"seals {b['name']} again instead of amending it")
    elif e["kind"] == "look":
        if seen.get(b["plan"], {}).get("kind") != "plan":
            raise Refusal(f"looks at {b['plan']}, which is not an earlier plan")
    elif b.get("plan"):  # a tagged run or launch
        p = seen.get(b["plan"])
        if not p or p["kind"] != "plan":
            raise Refusal(f"is tagged to {b['plan']}, which is not an earlier plan")
        if current(earlier, p["body"]["name"])["id"] != p["id"]:
            raise Refusal(f"is tagged to {b['plan']}, which an earlier amendment had already replaced")
        if b.get("hypothesis") not in [h["id"] for h in p["body"]["hypotheses"]]:
            raise Refusal(f"tests {b.get('hypothesis')}, which {b['plan']} does not have")
        if b.get("look"):
            lk = seen.get(b["look"])
            if not lk or lk["kind"] != "look" or lk["body"]["name"] != p["body"]["name"]:
                raise Refusal(f"spends {b['look']}, which is not an earlier look at its plan")
            if e["kind"] == "run" and b.get("launch"):
                pass  # a launch's run carries the launch's look
            elif any(v["kind"] in ("run", "launch") and v["body"].get("look") == b["look"] for v in earlier):
                raise Refusal(f"spends {b['look']} a second time")
