"""An append-only, hash-chained ledger of sources, citations and claims, checked at write time.

The ledger is a folder: `chain.jsonl` holds one entry per line, each carrying the sha256 of the one before, and
`blobs/` holds the documents' page texts by their sha256. An entry is checked before it is written and is never
changed after; `verify` walks the chain from the start, recomputes every hash and runs every citation's check again
against the stored pages and, where a source's file is still there, against the document itself.

Kinds:
- source: a document as read (its bytes' sha256, its page texts as a blob).
- citation: a verbatim quote on one page of a source, and the numbers it is cited for, each written in the quote.
- claim: a statement resting on citations; every number the statement writes is written in a quote it cites.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import quotecheck as qc
from .quotecheck import Refusal

try:
    import fcntl

    def _lock(f):
        fcntl.flock(f, fcntl.LOCK_EX)
except ImportError:  # Windows
    import msvcrt

    def _lock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

GENESIS = "0" * 64
USER_AGENT = "research-ledger/0.1 (citation check)"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def entry_hash(seq: int, kind: str, at: str, body: dict, prev: str) -> str:
    return hashlib.sha256(canonical({"seq": seq, "kind": kind, "at": at, "body": body, "prev": prev})).hexdigest()


class Ledger:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.chain = self.root / "chain.jsonl"
        self.blobs = self.root / "blobs"

    # --- reading --------------------------------------------------------------------------------------------------
    def entries(self) -> list:
        if not self.chain.exists():
            return []
        return [json.loads(line) for line in self.chain.read_text(encoding="utf-8").splitlines() if line.strip()]

    def get(self, id: str, kind: str = "") -> dict:
        e = next((e for e in self.entries() if e["id"] == id), None)
        if e is None or (kind and e["kind"] != kind):
            what = f"a {kind}" if kind else "an entry"
            raise Refusal(f"`{id}` is not {what} of the ledger", "ids are as add_source, cite and claim return them",
                          "list the ledger's entries to find it")
        return e

    def blob(self, sha: str) -> bytes:
        return (self.blobs / sha).read_bytes()

    def forms(self, source_id: str) -> list:
        return json.loads(self.blob(self.get(source_id, "source")["body"]["pages"]))

    # --- writing --------------------------------------------------------------------------------------------------
    def _put_blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        self.blobs.mkdir(parents=True, exist_ok=True)
        path = self.blobs / sha
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return sha

    def _append(self, kind: str, body: dict, check=lambda: None) -> dict:
        """Check and append one entry under an exclusive lock, so two writers never fork the chain."""
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / ".lock", "w") as lock:
            _lock(lock)
            check()
            entries = self.entries()
            prev = entries[-1]["hash"] if entries else GENESIS
            seq = len(entries)
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            h = entry_hash(seq, kind, at, body, prev)
            e = {"seq": seq, "id": f"{kind}:{h[:12]}", "kind": kind, "at": at, "body": body, "prev": prev, "hash": h}
            with open(self.chain, "a", encoding="utf-8") as f:
                f.write(canonical(e).decode("utf-8") + "\n")
                f.flush()
                os.fsync(f.fileno())
            return e

    def add_source(self, origin: str) -> dict:
        """Read a document (a local file, or an http(s) URL) and record it. The same bytes twice return the
        entry already there."""
        if origin.startswith(("http://", "https://")):
            req = urllib.request.Request(origin, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
            except OSError as e:
                raise Refusal(f"could not fetch {origin}", str(e)[:200], "check the URL, or download it and give the file")
        else:
            path = Path(origin).expanduser()
            if not path.is_file():
                raise Refusal(f"no file at {origin}", "a source is a local file or an http(s) URL",
                              "give the document's path or URL")
            data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        known = next((e for e in self.entries() if e["kind"] == "source" and e["body"]["sha256"] == sha), None)
        if known:
            return {**known, "already": True}
        forms = qc.forms_of(data, origin)
        if not any(p.strip() for f in forms for p in f):
            raise Refusal("the document has no text", "a scanned PDF has none to quote", "give a text layer (OCR it first)")
        pages = self._put_blob(canonical(forms))
        body = {"origin": origin, "sha256": sha, "pages": pages, "n_pages": max(map(len, forms)),
                "layout": len(forms) > 1}
        return self._append("source", body)

    def cite(self, source: str, page: int, quote: str, cited_numbers=()) -> dict:
        """Record a quote on one page of a source, refused unless it is on that page and writes every number."""
        src = self.get(source, "source")
        out = qc.check(self.forms(source), page, quote, cited_numbers)
        body = {"source": src["id"], "page": page, "quote": quote, "numbers": out["numbers"]}
        return self._append("citation", body)

    def claim(self, text: str, citations: list) -> dict:
        """Record a statement resting on citations, refused unless every number it writes is written in a quote
        it cites."""
        if not text.strip():
            raise Refusal("a claim needs its statement")
        if not citations:
            raise Refusal("a claim rests on at least one citation", "a statement with no quote behind it is not "
                          "recorded", "cite the passage it rests on first, then claim with the citation's id")
        cited = [self.get(c, "citation") for c in citations]
        written = set().union(*(qc.numbers(c["body"]["quote"]) for c in cited))
        loose = sorted(qc.numbers(text) - written)
        if loose:
            raise Refusal(f"{loose[0]} is not written in any quote the claim cites",
                          "every number a claim writes is one its quotes write",
                          "cite the passage that states it, write it as the quote does, or drop it from the claim")
        return self._append("claim", {"text": text, "citations": [c["id"] for c in cited]})

    # --- checking -------------------------------------------------------------------------------------------------
    def document(self, origin: str) -> bytes | None:
        """A source's bytes read again from the file it was recorded from (a relative path from the working
        directory, else from the project the ledger sits in); None for a URL or a file that is no longer there."""
        if origin.startswith(("http://", "https://")):
            return None
        path = Path(origin).expanduser()
        for p in (path,) if path.is_absolute() else (path, self.root.parent / path):
            if p.is_file():
                return p.read_bytes()
        return None

    def verify(self) -> dict:
        """Walk the chain: each hash recomputed, each link to an earlier entry, each blob intact, each citation's
        check run again on the stored pages, each claim's numbers in its quotes. A source whose file is still there
        is read again: its bytes must hash as recorded, and each citation's check runs on its pages read afresh, so
        page texts written by hand show up too. `unread` lists the sources that could not be (URLs, files gone)."""
        problems, seen, prev, fresh, unread = [], {}, GENESIS, {}, []
        for i, e in enumerate(self.entries()):
            where = f"entry {i} ({e.get('id', '?')})"
            if not {"seq", "id", "kind", "at", "body", "prev", "hash"} <= e.keys():
                problems.append(f"{where}: not a ledger entry (fields missing)")
                prev = e.get("hash", "")
                continue
            if e.get("seq") != i or e.get("prev") != prev:
                problems.append(f"{where}: the chain is broken here (an entry removed, reordered or inserted)")
            if entry_hash(e["seq"], e["kind"], e["at"], e["body"], e["prev"]) != e.get("hash") \
                    or e["id"] != f"{e['kind']}:{e['hash'][:12]}":
                problems.append(f"{where}: its content does not match its hash (edited after it was written)")
            prev = e.get("hash", "")
            b = e["body"]
            try:
                if e["kind"] == "source":
                    if hashlib.sha256(self.blob(b["pages"])).hexdigest() != b["pages"]:
                        problems.append(f"{where}: its page texts were changed")
                    data = self.document(b["origin"])
                    if data is None:
                        unread.append(e["id"])
                    elif hashlib.sha256(data).hexdigest() != b["sha256"]:
                        raise Refusal("its file has changed since it was recorded", b["origin"])
                    else:
                        fresh[e["id"]] = qc.forms_of(data, b["origin"])
                elif e["kind"] == "citation":
                    if seen.get(b["source"], {}).get("kind") != "source":
                        raise Refusal(f"cites {b['source']}, which is not an earlier source")
                    qc.check(self.forms(b["source"]), b["page"], b["quote"], b["numbers"])
                    if b["source"] in fresh:
                        try:
                            qc.check(fresh[b["source"]], b["page"], b["quote"], b["numbers"])
                        except Refusal as r:
                            raise Refusal(f"in the document itself, {r.error}", r.meaning)
                elif e["kind"] == "claim":
                    if any(seen.get(c, {}).get("kind") != "citation" for c in b["citations"]):
                        raise Refusal("rests on an id that is not an earlier citation")
                    written = set().union(*(qc.numbers(seen[c]["body"]["quote"]) for c in b["citations"]))
                    if qc.numbers(b["text"]) - written:
                        raise Refusal("writes a number none of its quotes write")
            except Refusal as r:
                problems.append(f"{where}: {r.error}" + (f" ({r.meaning})" if r.meaning else ""))
            except FileNotFoundError:
                problems.append(f"{where}: a blob it needs is missing")
            seen[e["id"]] = e
        counts = {k: sum(1 for v in seen.values() if v["kind"] == k) for k in ("source", "citation", "claim")}
        return {"ok": not problems, "entries": len(seen), **counts, "head": prev if seen else GENESIS,
                "problems": problems, "unread": unread}
