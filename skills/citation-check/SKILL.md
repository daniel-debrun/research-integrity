---
name: citation-check
description: Quote sources verbatim and have each quote checked against the page it cites before it backs a statement. Use whenever a statement in research, a literature review, a report or a paper rests on a document (a paper, a PDF, a report, a web page) and especially when it carries a number taken from one. Every quote is checked by code, not by judgement — it must be on the cited page and write every number it is cited for — and claims are recorded only on checked quotes.
license: Apache-2.0
compatibility: The checks need Python 3.10+. PDFs need pdftotext (poppler) or the pypdf package. The ledger tools come from the research-ledger MCP server; without it, scripts/quotecheck.py runs the same check.
---

# Citation check

A statement that rests on a source is only as good as the passage behind it. This skill makes that passage
checkable: you quote it verbatim from a named page, and code confirms the quote is on that page and writes every
number you use it for. A paraphrase, a quote on the wrong page, or a number that's rounded or taken from a
neighbouring row is refused.

## With the ledger tools (preferred)

When the `research-ledger` tools are available, every check is recorded in the project's `.ledger/`.

1. **Record the document once** with `add_source`, and use the returned `source` id from then on.
2. **Read before quoting** with `read_page`, and quote from that text, which is exactly what the check reads. For a
   table row, read the page with `layout: true` and quote the row as it appears there.
3. **Cite** with the source, the page, the quote copied verbatim, and `numbers`: every number the statement will
   take from this quote, written as the quote writes it. If you're unsure of the page, cite your best guess; a
   refusal names the page the quote is on.
4. **Claim** with the statement and the citation ids it rests on. Every number the statement writes must be written
   in one of those quotes. A number you computed (a difference, a ratio) is not quoted: say it in words, or leave it
   out of the claim.
5. **Verify** before you report, and report its result. If it lists problems, fix them with new citations or say
   plainly that they remain.

## Without the tools

Run the same check with the bundled script, which uses only the Python standard library:

```bash
python scripts/quotecheck.py PAPER.pdf --page 4 --quote "the exact passage" --number 0.61
```

It prints `{"ok": true, ...}` or a refusal and exits 1 on a refusal. Nothing is recorded, so keep the quote, the
page and the output next to the statement it backs.

## Rules

- **Quote, never paraphrase.** Whitespace, ligatures and words broken across lines don't matter; words, order and
  numbers do.
- **One place per quote**, at least 20 characters. Don't join passages from different pages or paragraphs.
- **A refusal is the answer.** Do what its `try` says. Never reword a quote to get it through, and never edit
  `.ledger/` by hand: `verify` shows the edit.
- **If no passage states it, the source doesn't support it.** Say so instead of citing something nearby.
