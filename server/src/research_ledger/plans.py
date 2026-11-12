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

A trial count belongs to the question, not to the plan's name or the folder. Sealing a question that an earlier plan
already asks is refused unless the new plan `continues` that plan, in which case a hypothesis that keeps an earlier id
inherits its trials. A plan started in a fresh ledger declares the trials counted elsewhere as `prior_trials`, with
where they were counted; the declaration is the author's word, recorded so the count says so.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .quotecheck import Refusal

TAGS = ("plan", "hypothesis", "look")
#: a subject key, such as `loss.ablation` or `firm.status`: lowercase letters, digits, dots and hyphens
SUBJECT = re.compile(r"[a-z0-9][a-z0-9.-]{0,79}")


def subject_key(subject: str) -> str:
    s = subject.strip()
    if s and not SUBJECT.fullmatch(s):
        raise Refusal(f"`{subject}` is not a subject key", "lowercase letters, digits, dots and hyphens, such as "
                      "loss.ablation", "write it as a key")
    return s


def _same_subject(entries, subject: str, name: str):
    """The latest version of another plan sealed on this subject, if any."""
    if not subject:
        return None
    names = {e["body"]["name"] for e in entries if e["kind"] == "plan" and e["body"]["name"] != name
             and e["body"].get("subject") == subject}
    return next((current(entries, n) for n in sorted(names)), None)


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


def _question_key(q: str) -> str:
    return " ".join(q.lower().split()).rstrip(".?! ")


def _same_question(entries, question: str, name: str):
    """The latest version of another plan asking this question, if any."""
    key = _question_key(question)
    names = {e["body"]["name"] for e in entries if e["kind"] == "plan" and e["body"]["name"] != name
             and _question_key(e["body"]["question"]) == key}
    return next((current(entries, n) for n in sorted(names)), None)


def _prior(prior, hypotheses: list) -> dict:
    """Trials declared from a ledger that is not this one: {"from": where, "<hypothesis id>": count, ...}."""
    if not prior:
        return {}
    if not isinstance(prior, dict) or not str(prior.get("from", "")).strip():
        raise Refusal("prior_trials says where the trials were counted", 'give {"from": a ledger head hash, URL or '
                      'path, "H1": 12, ...}', "take the counts from that ledger's plan_status")
    ids = {h["id"] for h in hypotheses}
    out = {"from": str(prior["from"]).strip()}
    for k, v in prior.items():
        if k == "from":
            continue
        if k not in ids:
            raise Refusal(f"prior_trials names {k}, which is not a hypothesis of the plan",
                          f"its hypotheses are {', '.join(sorted(ids))}")
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise Refusal(f"prior_trials[{k}] is not a count", "a whole number of trials, 0 or more")
        out[k] = v
    if len(out) == 1:
        raise Refusal("prior_trials declares no count", 'give a count per hypothesis id, such as {"H1": 12}')
    return out


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
    from . import guard
    for p in paths or []:
        path = guard.check(p)
        if not path.is_file():
            raise Refusal(f"no file at {p}", "a holdout is a file the work must not see until a look")
        out.append({"path": str(path), "sha256": _sha_file(path)})
    return out


def seal(led, name: str, question: str, hypotheses: list, holdout=(), continues: str = "", prior_trials=None,
         subject: str = "") -> dict:
    """Record a plan before the runs it governs. `continues` names an earlier plan whose question this one takes up:
    a hypothesis keeping one of its ids inherits that id's trials. `prior_trials` declares trials counted in another
    ledger. `subject` keys the question (`loss.ablation`), so a reworded question on the same subject is caught too."""
    subject = subject_key(subject)
    if not name.strip() or not question.strip():
        raise Refusal("a plan needs a name and a question")
    entries = led.entries()
    if current(entries, name):
        raise Refusal(f"a plan named {name} is already sealed", "a plan changes only by amendment, with its reason",
                      "amend it with amend_plan")
    prev = None
    if continues:
        prev = led.get(continues, "plan")
        if current(entries, prev["body"]["name"])["id"] != prev["id"]:
            raise Refusal(f"{continues} was replaced by an amendment", "a plan continues the latest version",
                          f"continue {current(entries, prev['body']['name'])['id']}")
    same = _same_question(entries, question, name) or _same_subject(entries, subject, name)
    if same and not prev:
        raise Refusal(f"a plan asking this question is already sealed as {same['body']['name']} ({same['id']})",
                      "a trial count belongs to the question; a new name would start it at zero",
                      f"amend {same['body']['name']}, or seal this plan with continues=\"{same['id']}\" so their "
                      "trials are counted together")
    hyps = _hypotheses(hypotheses, prev["body"]["hypotheses"] if prev else ())
    return led._append("plan", {"name": name, "question": question.strip(), "hypotheses": hyps,
                                "holdout": _holdout(holdout), "version": 1, "supersedes": "", "reason": "",
                                "because": [], "continues": prev["id"] if prev else "",
                                "prior_trials": _prior(prior_trials, hyps), **({"subject": subject} if subject else {})})


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
            "supersedes": old["id"], "reason": reason.strip(), "because": list(because or []),
            "continues": b.get("continues", ""), "prior_trials": b.get("prior_trials", {}),
            **({"subject": b["subject"]} if b.get("subject") else {})}
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


def spend(led, look_id: str, run_entry: dict) -> dict:
    """The tag for a reproduction that spends `look_id`, a fresh look at the plan whose look the original run spent.
    It carries the look alone: a reproduction is no trial."""
    if not look_id:
        return {}
    entries = led.entries()
    lk = led.get(look_id, "look")
    spent = led.get(run_entry["body"]["look"], "look")
    if lk["body"]["name"] != spent["body"]["name"]:
        raise Refusal(f"{look_id} is a look at {lk['body']['name']}, not {spent['body']['name']}")
    used = next((e for e in entries if e["kind"] in ("run", "launch") and e["body"].get("look") == look_id), None)
    if used:
        raise Refusal(f"{look_id} is spent by {used['id']}", "one run per look; a look whose run fails stays spent",
                      "take another look with look_holdout, and its reason")
    return {"look": look_id}


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


def lineage(entries, name: str) -> list:
    """The plans this one continues, nearest first, by name."""
    out, seen = [], {name}
    by_id = {e["id"]: e for e in entries if e["kind"] == "plan"}
    p = versions(entries, name)
    cont = p[0]["body"].get("continues", "") if p else ""
    while cont and cont in by_id and by_id[cont]["body"]["name"] not in seen:
        n = by_id[cont]["body"]["name"]
        out.append(n)
        seen.add(n)
        cont = versions(entries, n)[0]["body"].get("continues", "")
    return out


def counts(entries, name: str, hypothesis: str) -> dict:
    """Trials of one hypothesis: every tagged run and every launch not yet recorded as a run, plus the trials of the
    same hypothesis id in the plans this one continues (`inherited`) and the ones declared from another ledger
    (`declared`). `trials` is their sum."""
    ids = {v["id"] for v in versions(entries, name)}
    runs = [e for e in entries if e["kind"] == "run" and e["body"].get("plan") in ids
            and e["body"].get("hypothesis") == hypothesis]
    ran = {e["body"].get("launch") for e in entries if e["kind"] == "run"}
    open_ = [e for e in entries if e["kind"] == "launch" and e["body"].get("plan") in ids
             and e["body"].get("hypothesis") == hypothesis and e["id"] not in ran]
    reported = _reported_runs(entries)
    ok = [r for r in runs if _ok(r)]
    inherited = 0
    for n in lineage(entries, name):
        c = counts(entries, n, hypothesis)
        inherited += c["own"] + c["declared"]
    v = versions(entries, name)
    declared = sum(c for k, c in (v[0]["body"].get("prior_trials", {}) if v else {}).items()
                   if k == hypothesis)
    own = len(runs) + len(open_)
    return {"trials": own + inherited + declared, "own": own, "inherited": inherited, "declared": declared,
            "succeeded": len(ok), "failed": len(runs) - len(ok),
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
    # by instrument name, so a version registered again after a tweak is still the same instrument's runs
    names = {e["id"]: e["body"]["name"] for e in entries if e["kind"] == "instrument"}
    used = {names.get(e["body"]["instrument"], e["body"]["instrument"]) for e in tagged}
    untagged = [e["id"] for e in entries if e["kind"] == "run" and e["seq"] > first and not e["body"].get("plan")
                and not e["body"].get("reproduces")
                and names.get(e["body"]["instrument"], e["body"]["instrument"]) in used]
    looks = [{"look": e["id"], "reason": e["body"]["reason"],
              "used_by": next((r["id"] for r in entries if r["kind"] in ("run", "launch")
                               and r["body"].get("look") == e["id"]), None)}
             for e in entries if e["kind"] == "look" and e["body"]["plan"] in ids]
    return {"plan": name, "id": p["id"], "version": p["body"]["version"], "question": p["body"]["question"],
            "amendments": [{"id": v["id"], "version": v["body"]["version"], "reason": v["body"]["reason"],
                            "because": v["body"]["because"]} for v in versions(entries, name)[1:]],
            "hypotheses": hyps, "untagged_runs": untagged, "looks": looks,
            "continues": lineage(entries, name), "prior_trials": p["body"].get("prior_trials", {})}


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
                    **{k: c[k] for k in ("inherited", "declared") if c[k]},
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
        else:
            cont = b.get("continues", "")
            if cont and seen.get(cont, {}).get("kind") != "plan":
                raise Refusal(f"continues {cont}, which is not an earlier plan")
            same = _same_question(earlier, b["question"], b["name"]) or \
                _same_subject(earlier, b.get("subject", ""), b["name"])
            if same and not cont:
                raise Refusal(f"seals the question of {same['body']['name']} again under a new name")
            prior = b.get("prior_trials", {})
            if prior and (not str(prior.get("from", "")).strip() or any(
                    k != "from" and (k not in ids or not isinstance(v, int) or v < 0) for k, v in prior.items())):
                raise Refusal("its prior_trials are not counts per hypothesis with where they were counted")
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
    elif b.get("look"):  # a reproduction spending a fresh look
        lk = seen.get(b["look"])
        if not lk or lk["kind"] != "look":
            raise Refusal(f"spends {b['look']}, which is not an earlier look")
        if any(v["kind"] in ("run", "launch") and v["body"].get("look") == b["look"] for v in earlier):
            raise Refusal(f"spends {b['look']} a second time")
