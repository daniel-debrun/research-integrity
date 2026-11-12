"""Rules 2 (v0.2.1): a quote may not cut a number, citations may casefold, a claim's numbers are its citations'
cited numbers with labels aside, and entries recorded under rules 1 are still rechecked as they were made."""

import json

import pytest

from research_ledger import quotecheck as qc
from research_ledger.ledger import Ledger
from research_ledger.quotecheck import Refusal


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


def test_a_quote_cut_inside_a_number_is_refused(led, paper):
    src = led.add_source(str(paper))
    with pytest.raises(Refusal, match="cuts a number"):
        led.cite(src["id"], 2, "the network reaches a mean CRPS of 0.6", ["0.6"])
    with pytest.raises(Refusal, match="cuts a number"):
        led.cite(src["id"], 1, "234 stations over two years", ["234"])
    assert led.cite(src["id"], 2, "the network reaches a mean CRPS of 0.63", ["0.63"])


def test_a_quote_ending_at_a_full_stop_after_a_number_passes():
    page = "The error falls to 0.61. Then it rises."
    assert qc.check([[page]], 1, "The error falls to 0.61", ["0.61"])["ok"]


def test_casefold_matches_letters_not_numbers(led, paper):
    src = led.add_source(str(paper))
    with pytest.raises(Refusal, match="not on page 2"):
        led.cite(src["id"], 2, "SITE CALIBRATION 0.70", ["0.70"])
    c = led.cite(src["id"], 2, "SITE CALIBRATION 0.70", ["0.70"], casefold=True)
    assert c["body"]["casefold"] is True and led.verify()["ok"]


def test_labels_are_not_claim_numbers_but_years_are():
    assert qc.claim_numbers("Table 4 and Figs. 2 and 3 show 0.63 (Section 3.1, § 2)") == {qc.as_number("0.63")}
    assert qc.as_number("2024") in qc.claim_numbers("In 2024 the error fell (Table 4).")


def test_a_claim_may_name_a_table_without_citing_it(led, paper):
    src = led.add_source(str(paper))
    c = led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    assert led.claim("Site calibration reaches 0.70 (Table 4).", [c["id"]])
    assert led.verify()["ok"]


def test_rules_1_entries_are_rechecked_as_recorded(led, paper):
    """A v0.2.0 ledger stays valid: its citations are rechecked without the number-edge rule, and its claims
    against every number their quotes write."""
    src = led.add_source(str(paper))
    c = led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    k = led.claim("Site calibration reaches 0.70.", [c["id"]])
    lines = led.chain.read_text().splitlines()
    from research_ledger.ledger import entry_hash, GENESIS
    out, prev = [], GENESIS
    for line in lines:  # rewrite the chain as v0.2.0 would have written it
        e = json.loads(line)
        e["body"].pop("rules", None)
        if e["kind"] == "citation":
            e["body"]["quote"] = "the network reaches a mean CRPS of 0.6"
            e["body"]["numbers"] = ["0.6"]
        if e["kind"] == "claim":
            e["body"]["text"] = "The network reaches 0.6."
        e["prev"] = prev
        e["hash"] = entry_hash(e["seq"], e["kind"], e["at"], e["body"], prev)
        e["id"] = f"{e['kind']}:{e['hash'][:12]}"
        if e["kind"] == "claim":
            e["body"]["citations"] = [out[1]["id"]]
            e["hash"] = entry_hash(e["seq"], e["kind"], e["at"], e["body"], prev)
            e["id"] = f"{e['kind']}:{e['hash'][:12]}"
        prev = e["hash"]
        out.append(e)
    led.chain.write_text("".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in out))
    assert led.verify()["ok"], led.verify()["problems"]
