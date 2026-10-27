"""Is a quote on the page it cites, and is each number it is used for written in it?

Standard library only, so the same file runs as the citation-check skill's script and inside the ledger server.
A page matches a quote whitespace, ligatures, soft hyphens and line-break hyphens aside; "0.70" is 0.7, "1,234" is
1234 and the unicode minus is "-". A refusal says what is wrong, what it means and what to do instead.

    python quotecheck.py PAPER.pdf --page 4 --quote "the error falls to 0.61 with station data" --number 0.61

A quote on another page is refused with the page it is on.

A .txt file's pages are separated by form feeds (pdftotext's output). A PDF needs pdftotext (poppler) or pypdf.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

MIN_QUOTE = 20      # characters: a shorter quote is found almost anywhere
#: a number as written: thousands separators allowed, and a minus only where it is a sign (not "0.5-0.6")
_NUMERAL = re.compile(r"(?<![\w.])(-?)((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)")


class Refusal(Exception):
    """A check that failed: what is wrong, what it means, what to do instead."""

    def __init__(self, error: str, meaning: str = "", fix: str = ""):
        super().__init__(error)
        self.error, self.meaning, self.fix = error, meaning, fix

    def envelope(self) -> dict:
        return {"ok": False, "error": self.error, "meaning": self.meaning, "try": self.fix}


def flat(text: str) -> str:
    """Text as a quote is matched: NFKC (ligatures), no soft hyphens, the unicode minus as '-', a word broken
    across lines ("post-\\nprocessing") joined, a hyphen between letters dropped (so "post-processing" and
    "postprocessing" match either way), whitespace collapsed."""
    t = unicodedata.normalize("NFKC", text).replace("­", "").replace("−", "-")
    t = re.sub(r"(?<=[^\W\d_])-\s*\n\s*(?=[^\W\d_])", "", t)
    t = re.sub(r"(?<=[^\W\d_])-(?=[^\W\d_])", "", t)
    return " ".join(t.split())


def numbers(text: str) -> set:
    """The numbers written in `text`, by value."""
    return {Decimal(sign + digits.replace(",", "")) for sign, digits in _NUMERAL.findall(flat(text))}


def as_number(v) -> Decimal:
    """A number given by the caller (a float, an int or a string such as "1,234"), by value."""
    if isinstance(v, bool):
        raise Refusal(f"{v!r} is not a number")
    try:
        if isinstance(v, float):
            return Decimal(int(v)) if v.is_integer() else Decimal(repr(v))
        return Decimal(str(v).replace(",", "").replace("−", "-").strip())
    except InvalidOperation:
        raise Refusal(f"{v!r} is not a number", "a number the quote is cited for", "give it as the quote writes it")


def matching(quote: str) -> str:
    """The quote in matching form, refused when too short to mean anything."""
    q = flat(quote)
    if len(q) < MIN_QUOTE:
        raise Refusal(f"the quote is too short to check ({len(q)} characters)",
                      f"a quote of fewer than {MIN_QUOTE} characters is found almost anywhere",
                      "quote the whole passage the statement rests on")
    return q


def pages_with(forms: list, quote: str) -> list:
    """The 1-based pages whose text, in any of its forms (reading order, layout), holds the quote."""
    q = matching(quote)
    n = max(len(f) for f in forms)
    return [i + 1 for i in range(n) if any(i < len(f) and q in flat(f[i]) for f in forms)]


def check(forms: list, page: int, quote: str, cited_numbers=()) -> dict:
    """Refuse unless the quote is on `page` and every cited number is written in it. `forms` holds the document's
    pages once per extraction (reading order, and the layout when there is one, which keeps table rows whole)."""
    n = max(len(f) for f in forms)
    if not isinstance(page, int) or not 1 <= page <= n:
        raise Refusal(f"the document has no page {page}", f"its pages are 1..{n}", "cite a page it has")
    on = pages_with(forms, quote)
    if page not in on:
        raise Refusal(f"the quote is not on page {page}",
                      f"it is on page {', '.join(map(str, on))}" if on else
                      "it is on no page of the document, whitespace, ligatures and line-break hyphens aside",
                      "cite the page it is on" if on else
                      "copy the passage as the page's text gives it (read the page first); never paraphrase")
    written = numbers(quote)
    for v in cited_numbers:
        if as_number(v) not in written:
            raise Refusal(f"{v} is not written in the quote", f"the quote writes {', '.join(sorted(map(str, written))) or 'no number'}",
                          "quote the text that states the number, or drop it")
    return {"ok": True, "page": page, "numbers": [str(as_number(v)) for v in cited_numbers]}


def pdf_forms(data: bytes) -> list:
    """A PDF's pages: [reading order, layout] with pdftotext, else [reading order] with pypdf."""
    if shutil.which("pdftotext"):
        out = []
        for flags in ([], ["-layout"]):
            done = subprocess.run(["pdftotext", *flags, "-", "-"], input=data, capture_output=True, timeout=120)
            if done.returncode != 0:
                raise Refusal("the PDF could not be read", done.stderr.decode("utf-8", "replace")[:200])
            out.append(split_pages(done.stdout.decode("utf-8", "replace")))
        return out
    try:
        import io

        from pypdf import PdfReader
    except ImportError:
        raise Refusal("no PDF reader", "reading a PDF needs pdftotext (poppler) or the pypdf package",
                      "install one, or give the text with pages separated by form feeds")
    return [[p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages]]


def split_pages(text: str) -> list:
    """Text whose pages are separated by form feeds, as pdftotext writes it."""
    pages = text.split("\f")
    return pages[:-1] if len(pages) > 1 and not pages[-1].strip() else pages


def forms_of(data: bytes, name: str) -> list:
    """A document's page forms from its bytes: a PDF, or text with form-feed page breaks."""
    if data[:5] == b"%PDF-" or name.lower().endswith(".pdf"):
        return pdf_forms(data)
    return [split_pages(data.decode("utf-8", "replace"))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check a quote is on the page it cites and writes its numbers.")
    ap.add_argument("document", help="a PDF, or text with pages separated by form feeds")
    ap.add_argument("--page", type=int, required=True, help="the page the quote cites (1-based)")
    ap.add_argument("--quote", required=True, help="the passage, verbatim")
    ap.add_argument("--number", action="append", default=[], help="a number the quote is cited for (repeatable)")
    a = ap.parse_args(argv)
    try:
        out = check(forms_of(Path(a.document).read_bytes(), a.document), a.page, a.quote, a.number)
    except Refusal as r:
        out = r.envelope()
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
