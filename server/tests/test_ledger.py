import shutil

import pytest
from conftest import tiny_pdf

from research_ledger.ledger import Ledger, canonical
from research_ledger.quotecheck import Refusal


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / ".ledger")


def lines(led):
    return led.chain.read_text().splitlines()


def test_a_citation_and_a_claim_on_it_are_recorded_and_verify(led, paper):
    src = led.add_source(str(paper))
    assert src["body"]["n_pages"] == 2 and led.add_source(str(paper))["already"]
    c = led.cite(src["id"], 2, "Site calibration 0.70", [0.7])
    k = led.claim("Site calibration reaches a mean CRPS of 0.70.", [c["id"]])
    assert [e["kind"] for e in led.entries()] == ["source", "citation", "claim"]
    assert led.entries()[2]["prev"] == led.entries()[1]["hash"] and k["body"]["citations"] == [c["id"]]
    v = led.verify()
    assert v["ok"] and (v["source"], v["citation"], v["claim"]) == (1, 1, 1) and v["head"] == k["hash"]


def test_a_refused_citation_writes_nothing(led, paper):
    src = led.add_source(str(paper))
    with pytest.raises(Refusal, match="not on page 1"):
        led.cite(src["id"], 1, "Site calibration 0.70")
    assert len(lines(led)) == 1


def test_a_claim_needs_citations_and_every_number_it_writes_in_their_quotes(led, paper):
    src = led.add_source(str(paper))
    c = led.cite(src["id"], 2, "Site calibration 0.70")
    with pytest.raises(Refusal, match="at least one citation"):
        led.claim("Site calibration wins.", [])
    with pytest.raises(Refusal, match="0.63 is not a number any citation of the claim is cited for"):
        led.claim("The network reaches 0.63, ahead of site calibration at 0.70.", [c["id"]])
    with pytest.raises(Refusal, match="is not a citation"):
        led.claim("Site calibration wins.", [src["id"]])
    net = led.cite(src["id"], 2, "the network reaches a mean CRPS of 0.63", ["0.63"])
    with pytest.raises(Refusal, match="0.7 is not a number any citation"):  # in c's quote, but c is not cited for it
        led.claim("The network reaches 0.63, ahead of site calibration at 0.7.", [c["id"], net["id"]])
    site = led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    assert led.claim("The network reaches 0.63, ahead of site calibration at 0.7.", [site["id"], net["id"]])


def test_verify_finds_an_edited_entry_a_removed_one_and_a_changed_page(led, paper, tmp_path):
    src = led.add_source(str(paper))
    c = led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    led.claim("Site calibration reaches 0.70.", [c["id"]])
    keep = tmp_path / "keep"
    shutil.copytree(led.root, keep)

    led.chain.write_text(led.chain.read_text().replace("Site calibration 0.70", "Site calibration 0.80"))
    assert any("does not match its hash" in p for p in led.verify()["problems"])

    shutil.rmtree(led.root)
    shutil.copytree(keep, led.root)
    rows = lines(led)
    led.chain.write_text("\n".join([rows[0], rows[2]]) + "\n")
    assert any("chain is broken" in p for p in led.verify()["problems"])

    shutil.rmtree(led.root)
    shutil.copytree(keep, led.root)
    blob = led.blobs / src["body"]["pages"]
    blob.write_bytes(blob.read_bytes().replace(b"0.70", b"0.80"))
    problems = led.verify()["problems"]
    assert any("page texts were changed" in p for p in problems) and any("not on page 2" in p for p in problems)


def test_a_pdf_is_read_page_by_page(led, tmp_path):
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(tiny_pdf(["The calibrated forecast reaches a skill of 0.31", "at the coastal stations."]))
    src = led.add_source(str(pdf))
    assert led.cite(src["id"], 1, "The calibrated forecast reaches a skill of 0.31", ["0.31"])["kind"] == "citation"


def test_a_missing_file_is_refused(led, tmp_path):
    with pytest.raises(Refusal, match="no file at"):
        led.add_source(str(tmp_path / "nothing.pdf"))


def test_verify_reads_each_source_again_so_hand_written_pages_show_up(led, paper):
    src = led.add_source(str(paper))
    # A forger writes their own page texts and rebuilds every hash, so the folder agrees with itself.
    body = {**src["body"], "pages": led._put_blob(canonical([["Site calibration reaches a mean CRPS of 0.50.", ""]]))}
    led.chain.write_text("")
    forged = led._append("source", body)
    led.cite(forged["id"], 1, "Site calibration reaches a mean CRPS of 0.50", ["0.5"])
    v = led.verify()
    assert not v["ok"] and v["unread"] == []
    assert any("in the document itself, the quote is not on page 1" in p for p in v["problems"])


def test_a_changed_file_is_a_problem_and_a_missing_one_is_listed_unread(led, paper):
    src = led.add_source(str(paper))
    led.cite(src["id"], 2, "Site calibration 0.70", ["0.70"])
    assert led.verify() == {**led.verify(), "ok": True, "unread": []}
    paper.write_text(paper.read_text().replace("0.70", "0.71"))
    assert any("its file has changed" in p for p in led.verify()["problems"])
    paper.unlink()
    v = led.verify()
    assert v["ok"] and v["unread"] == [src["id"]]
