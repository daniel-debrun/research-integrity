# research-integrity

> `research-integrity` and `research-ledger` are placeholder names.

When an AI agent writes "the paper reports 0.61", nothing usually checks that the paper does. This plugin makes
that check mechanical. A quote is recorded only if it is on the page it cites and writes every number it is cited
for. A claim is recorded only on recorded quotes, and only if every number it writes is written in one of them.
A number measured by a program rests on that program's recorded output in the same way: the program is registered
as an instrument, one exact version of its code and environment, and every run of it is recorded. Each record goes
into a hash-chained ledger in the project (`.ledger/`) that anyone can recheck. The checks are code, not a
reviewer's judgement: a refusal says what is wrong, what it means and what to do instead.

Sealed plans, counted trials and a holdout come next.

## What is in it

| Part | What it does | Where it runs |
|---|---|---|
| `skills/citation-check/` | The method: read, quote verbatim, cite with numbers, claim on citations, verify. Ships `scripts/quotecheck.py`, the same check as the server, standard library only. | Any Agent Skills client: Claude Code, Claude Science, Cowork, Codex and others |
| `server/` (`research-ledger`) | An MCP server (stdio) over the ledger: `add_source`, `read_page`, `cite`, `claim`, `verify`, `list_entries`, and for instruments `register_instrument`, `run_instrument`, `run_acting_instrument`, `cite_run`. Every tool is annotated; only `run_acting_instrument` is destructive. | Any MCP client that runs a local command |
| `.claude-plugin/`, `.mcp.json` | The plugin and its own marketplace: installing the plugin installs both of the above. | Claude Code |

## Install

Claude Code (this repo is its own marketplace):

```bash
claude plugin marketplace add daniel-debrun/research-integrity
```

```bash
claude plugin install research-integrity@research-integrity
```

Or for one session from a checkout: `claude --plugin-dir /path/to/research-integrity`.

Other clients:

- **Skill only**: copy or import `skills/citation-check/` (in Claude Science: Skills, then Import from GitHub).
- **Ledger server**: add a local-command MCP server that runs
  `uv run --locked --project /path/to/research-integrity/server research-ledger` in the project folder. Set
  `LEDGER_DIR` to keep the ledger somewhere other than `./.ledger`.

Requirements: [uv](https://docs.astral.sh/uv/), and Python 3.10 or newer (uv fetches one if needed). For PDFs,
pdftotext (poppler) is used when installed, and the pypdf package otherwise.

## The ledger

`.ledger/chain.jsonl` has one entry per line: a source, a citation or a claim. Each entry holds the sha256 of the
entry before it. `.ledger/blobs/` keeps each document's page texts by their sha256, so every quote is checked
against exactly what was read. Entries are never changed. `verify` walks the chain, recomputes every hash, and
runs each citation's check and each claim's number check again. An edited, removed or reordered entry, or a
changed page, shows up as a problem. Where a source's file is still at the path it was recorded from, `verify`
reads it again: the bytes must hash as recorded, and every citation is rechecked against the document itself, so
page texts written by hand show up even when every hash was rebuilt. Sources it could not read again (URLs, files
moved or deleted) are listed as `unread`.

## Instruments

`register_instrument` records a program as one exact version: the sha256 of each of its code files (the bytes kept in
`blobs/`; environments, caches, version control, `.env` and key files are never taken), the program the command
starts (its path and its file's sha256) and, for Python, its version and installed distributions. The version is the
hash of all of that, so any change is a new version.

`run_instrument` checks that nothing it runs with has changed, naming what did if something has ("numpy 1.26.4 →
2.0.1"), then runs a read-only copy of the registered code, never the live folder, in a folder of its own under
`.ledger/work/`. It gets `PATH`, `HOME`, the locale and the declared secrets, nothing else of the environment. The
run is recorded failed or not: its args, exit code, and the sha256 of its stdout, stderr and declared outputs, with
the secrets' values masked. `cite_run` quotes a run's output the way `cite` quotes a page.

An instrument registered with `acts` (it places an order, sends a message) runs only through
`run_acting_instrument`, which is annotated destructive, so a client that asks before destructive actions asks
before each of its runs. `run_instrument` refuses it.

`verify` checks each instrument's code and each run's stored output against their hashes, and each citation of a run
against the stored output. It does not run anything again.

Entries are not signed, and nothing here asks you to approve anything: when to ask you is left to the agent's own
mode (in auto mode, nothing is asked). The chain shows that the ledger was changed after the fact. To fix its state
at a point in time, commit `.ledger/` with the work it backs.

## What the checks don't catch

- **Page texts written by hand, for an `unread` source.** Without the document, `verify` can only check the folder
  against itself, and a ledger whose pages and hashes were all rewritten agrees with itself. Keep the documents
  where they were recorded from. URLs are not fetched again, so download a document you rely on and record the file.
- **A number from the wrong place.** Numbers are matched by value. A claim can take a number from a quote that is
  about something else.
- **Statements without numbers.** "The method beats the baseline" is recorded on any citation. Only the citation is
  checked.
- **Numbers in labels.** "Table 2" or a year in a claim counts as a number, so it has to be in a cited quote too.
- **A rigged instrument.** An instrument that prints a number it was given, rather than measuring it, is recorded
  like any other. What the ledger gives you is the path from a claim to the run to the exact code that printed it.
- **Runs outside the ledger.** The agent can run a program directly. That run isn't recorded, so no claim can rest
  on it, but nothing stops it happening.
- **An environment changed in place.** A Python package edited without changing its version is not seen as a change.
  The program's own file and the code snapshot are hashed byte for byte.
- **What you record.** `add_source` takes any file, and its text is stored in `.ledger/blobs/`. Look before you
  commit `.ledger/`, and don't commit papers you may not redistribute.

## Data and network use

- **Reads**: the files you pass to `add_source`, and the code folder you pass to `register_instrument`.
- **Writes**: only inside the ledger folder (`./.ledger`, or `$LEDGER_DIR`).
- **Runs**: the instruments you register, when a run tool is called. They do whatever their code does, including
  network access; the server adds none of its own.
- **Network**: the server itself has one destination. `add_source` fetches a URL you give it, one GET to that URL
  with the user agent `research-ledger/0.1`. Nothing else is sent anywhere: no telemetry, no analytics, no account.
- **Secrets**: an instrument's secrets are read by name from the server's environment and passed to its runs only.
  Their values are never recorded and are masked in stored output. Set them where the server starts, such as the
  `env` of its entry in `.mcp.json`.
- **Installing**: `uv` downloads the server's pinned dependencies from PyPI (`server/uv.lock`) on first run.

## Privacy

The plugin collects nothing. Documents, instruments, runs, quotes and claims stay in the ledger folder on your
machine. The server's only outbound request is a URL fetch you ask for.

## Develop

```bash
cd server && uv run --group dev pytest -q
```

`skills/citation-check/scripts/quotecheck.py` is a copy of `server/src/research_ledger/quotecheck.py`, and a test
fails if they differ. After changing the check, copy it over:
`cp server/src/research_ledger/quotecheck.py skills/citation-check/scripts/`.

## License

Apache License 2.0. See [LICENSE](LICENSE).
