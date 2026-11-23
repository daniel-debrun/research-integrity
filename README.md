# research-integrity

When an AI agent writes "the paper reports 0.61", nothing usually checks that the paper does. This plugin makes
that check mechanical. A quote is recorded only if it is on the page it cites and writes every number it is cited
for, without cutting a longer number on the page ("0.6" from "0.61"). A claim is recorded only on recorded quotes,
and only if every number it writes is one they were cited for.
A number measured by a program rests on that program's recorded output in the same way: the program is registered
as an instrument, one exact version of its code and environment, and every run of it is recorded. Each record goes
into a hash-chained ledger in the project (`.ledger/`) that anyone can recheck. The checks are code, not a
reviewer's judgement: a refusal says what is wrong, what it means and what to do instead.

Reviews are recorded too. A draft is recorded at an exact version, a reviewer persona records its findings with its
own evidence, and the session that owns the work checks and answers every finding with evidence of its own before a
revision can be recorded. When a critique changes the direction of a plan, a result or a draft, the ledger shows why,
and shows that each point was checked rather than taken on the reviewer's word.

Memory is checked too. A fact about the machine (a file's content, a config value, what a tag points to, a count a
program printed) is recorded from a read-only probe the server runs itself, and probed again every time it is
recalled, so a stale one comes back flagged instead of repeated. A decision is recorded on a subject and names the
decision it replaces; the current ones are printed when a session starts. An agent's hand-back report can be
checked reference by reference against the machine before anyone relies on it.

Results are counted too. A plan is sealed before the runs it governs, every run is tagged with the hypothesis it
tests, and a claim records how many trials stand behind it, so a best-of-forty can't pass as one experiment.

## What is in it

| Part | What it does | Where it runs |
|---|---|---|
| `skills/citation-check/` | The method: read, quote verbatim, cite with numbers, claim on citations, verify. Ships `scripts/quotecheck.py`, the same check as the server, standard library only. | Any Agent Skills client: Claude Code, Claude Science, Cowork, Codex and others |
| `server/` (`research-ledger`) | An MCP server (stdio) over the ledger: `add_source`, `read_page`, `cite`, `claim`, `verify`, `list_entries`, and for instruments `register_instrument`, `run_instrument`, `run_acting_instrument`, `reproduce_run`, `cite_run`. Every tool is annotated; only `run_acting_instrument` and `look_holdout` are destructive. | Any MCP client that runs a local command |
| `server/` memory and search | `record_fact`, `recall`, `decide`, `list_subjects`, `list_decisions`, `write_digest`, `check_report`, `search_sources`, `annotate_source`, `anchor_ledger`. | Any MCP client that runs a local command |
| `skills/memory/` | Facts probed on recall, decisions on subjects, hand-back reports checked, passages found to quote. | Any Agent Skills client |
| `hooks/hooks.json` | At session start, prints current decisions, recent reversals and facts that no longer hold. Before a file tool runs, asks you when it is given a credential path. | Claude Code |
| `skills/plans/` | Decide before you look: seal the plan, tag every run, report the trial count, spend holdout looks deliberately. | Any Agent Skills client |
| `skills/critique/` | How a review is recorded and answered, and how to report it: each finding with what you found when you checked. | Any Agent Skills client |
| `agents/` | Five personas: `adversary` and `reviewer` (record critiques; can't edit, run shells or answer findings), `scout` (finds and cites sources), `replicator` (re-derives a number with its own code), `experimenter` (runs the planned measurements). | Claude Code |
| `commands/adversary-review.md` | `/research-integrity:adversary-review <draft> [files]`: records the draft, hands it to the adversary without your reasoning, then has you check and answer every finding. | Claude Code |
| `.claude-plugin/`, `.mcp.json` | The plugin and its own marketplace: installing the plugin installs all of the above. | Claude Code |
| `extras/compute/` | **Optional** second plugin, `research-compute`: compute nodes (this machine, SSH, Slurm) and instruments launched on them. See its [README](extras/compute/README.md). | Claude Code, or any MCP client |

## Install

Claude Code (this repo is its own marketplace):

```bash
claude plugin marketplace add daniel-debrun/research-integrity
```

```bash
claude plugin install research-integrity@research-integrity
```

Or for one session from a checkout: `claude --plugin-dir /path/to/research-integrity`.

The optional compute add-on is listed in the same marketplace:

```bash
claude plugin install research-compute@research-integrity
```

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

`reproduce_run` runs a recorded run again: the same instrument version, the same args, the same way, after the same
drift check. The rerun is recorded as a run marked `reproduces`, and a `reproduction` entry records the comparison:
the exit code and every declared output by sha256 decide the verdict, `reproduced` or `differs`; stdout stands in
when the instrument declares no outputs; stderr is compared and shown but never decides, since it carries timings. The
reply gives the first differing line of each output that differs, so a timestamp is told from a number at a glance.
A reproduction counts as no trial of any plan. It is refused for an instrument that acts (it would act again), for a
run made on a compute node (launch it again there), and for a run that spent a holdout look unless a fresh look is
passed: reproducing a holdout run is another look.

`verify` checks each instrument's code and each run's stored output against their hashes, each citation of a run
against the stored output, and each reproduction's comparison against the two runs' records. It does not run anything
again; `reproduce_run` does.

## Plans and trials

`seal_plan` records a study's question and its hypotheses, each with what it claims, what is measured and what
counts as a result, before any run that tests it. It can also name holdout files, recorded by sha256. A plan changes
only through `amend_plan`, a new version with its reason and what prompted it. Like a draft's revision, an amendment
is refused while a blocking finding on the plan is unanswered.

`run_instrument` and research-compute's `launch_instrument` take `plan` and `hypothesis`. The tag is set when the run
starts, never afterwards, so every attempt is a trial on the record, failures included. `plan_status` lists, for
each hypothesis, the trials that succeeded, failed or are still running, how many a claim reports, and the
successful runs nothing reports. It also lists runs of the same instruments made without a tag since the seal. A
claim that rests on a tagged run records the count when it is made, for example 1 of 40 trials, and `verify`
recomputes it.

`look_holdout` spends one look at the holdout, with its reason. It is refused if a holdout file changed since the
seal, and the look's id goes to the one run that reads the holdout. A look whose run fails stays spent. Looks are
counted, not limited, and the tool is annotated destructive, so a client that asks before destructive actions asks
before each look.

A trial count belongs to the question, not to the plan's name or the folder. `seal_plan` refuses a question that an
earlier plan already asks (compared without case, spacing or final punctuation) unless the new plan `continues` that
plan by id. A plan sealed with a `subject` key (`loss.ablation`) is caught on that key as well, however its question
is worded. Then a hypothesis given one of the earlier plan's ids inherits that id's trials, and `plan_status` shows
the lineage and the `inherited` count. A plan started in a fresh ledger, say one made for a publication, declares the
trials counted elsewhere as `prior_trials`, with where they were counted (a ledger head hash, a URL, a path); they are
counted as `declared` and a claim's count says so ("rests on 1 of 40 trials, 28 declared"). A declaration is the
author's word: the ledger cannot see another ledger.

## Reviews

`record_draft` records a text file (an issue, a report, a plan, a paper) at its exact text, and lists the numbers in
it that no citation's quote writes and its wording to check: each "novel", "first to", "unprecedented", "proves" or
"demonstrates conclusively", with its line and whether it sits inside a quote, a blockquote or code, where it is
someone else's word. Wording is listed, never refused: the draft is recorded as it is so the review can target that
text, and a reviewer makes a finding of each unquoted use. `claim` answers the same way for a statement that uses one. `record_critique` records a reviewer's findings on a draft, claim, run or
citation. Each finding says what is wrong and where, and whether it is blocking (it would change the conclusion or
the plan). It names the citations or runs the reviewer recorded as evidence; a finding with none is marked
unsupported.

`answer_finding` answers one finding, upheld or dismissed. A blocking finding can only be answered with evidence
recorded after the critique: a check the owner made in response to it, whichever way it goes. A reviewer's number
is a claim like any other, and reviewers are wrong too. A dismissal needs a reason.

`record_draft(path, supersedes=...)` and `claim(..., supersedes=...)` record a revision. Either is refused while a
blocking finding on the version it replaces is unanswered. `verify` reports an upheld finding whose draft or claim
was never revised, and a claim still resting on a run or citation that a finding was upheld against. It lists the
blocking findings still open, and each persona's record: findings upheld, dismissed and open.

Entries are not signed, and nothing here asks you to approve anything: when to ask you is left to the agent's own
mode (in auto mode, nothing is asked). The chain shows that the ledger was changed after the fact. To fix its state
at a point in time, commit `.ledger/` with the work it backs, or write its head to a file outside it with
`anchor_ledger` and keep that file elsewhere: `verify(anchor=...)` then shows an entry removed from the end or a
chain rewritten whole, which the chain alone cannot.

Each entry records the rules it was checked under (`rules`), and `verify` rechecks it under those rules, so a ledger
written by an older version stays valid. Rules 2 (v0.2.1) added the number-edge check on quotes, `casefold` on
citations, and claims resting on their citations' cited numbers.

## Memory

A **fact** is a probe's result: `file` (exists, sha256, size), `text_in_file` (exact, at least 4 characters, never
starting or ending inside a word or a number, `casefold` optional), `field` (a scalar at a dotted key of a JSON or
TOML file; `plugins["name@market"].0.version` for keys with dots), `git_ref` (what a ref resolves to, and whether it
is an ancestor of another) and `run` (a `cite_run` quote). The statement is written from the probe, so no other
wording rides on it; a `note` is kept and marked unchecked. `recall` finds facts, decisions and claims by shared
words (it finds and does not check) and re-probes each fact: `current`, `stale`, `unreachable` or `as_of`. `verify`
lists stale facts under `stale_facts`; a stale fact is the world changing, so it does not make `ok` false.

A **decision** is on a subject key. Subjects are a registry: a new one must be declared (`new_subject`), and the
refusal lists the subjects that share a word with it, so `verification-firm.status` is not created silently beside
`firm.status`. A decision on a subject that has one standing must `supersede` it. The session-start hook and
`write_digest` print the current decisions, those superseded in the last two weeks and subjects created in that time.

`check_report` extracts paths (existing or not), commits, branch and tag names, versions next to a declared name and
test counts, and checks each. A version is compared with what is installed (`installed_plugins.json`, the records
whose scope applies) when its sentence says installed, enabled or running, with the repository's manifests when it
says declares, in the repo or bumped, and with both otherwise; two sources that disagree make it `ambiguous`.

`search_sources` ranks passages of the recorded sources by BM25 over words. `annotate_source` gives a source a type
(vendor and aggregator pages go stale after 90 days), a freshness window or a DOI, which is checked at Crossref for
retraction and correction notices. `claim(subject=...)` keys a claim; standing claims on one subject with different
numbers come back as `conflicts`. `record_draft` lists each line that writes a number no standing claim writes, the
claims resting on stale, untyped or retracted sources, and the conflicts. When a recorded source's file changes,
`verify` runs each of its citations against the new text and reports which quotes are on the same page, moved or gone.

**Files that are never read.** Every file a tool reads, and every instrument snapshot, goes through a guard. It
refuses, by the path as given and as resolved, credentials and keys (`.netrc`, `.pgpass`, `.ssh/`, `.aws/`, `.pypirc`,
`.npmrc`, `.git-credentials`, `.credentials.json`, `.env`, `*.key`, `*.pem`, `id_*`, data files named for a token,
secret or password, among others), other ledgers, and folders listed as licensed data in
`~/.config/research-ledger/licensed_paths` (one per line, kept outside every project so it is never committed). While
that list is empty, `verify` says so. A `field` key that names a secret is refused, so its value is never recorded.

The same name rules cover the agent's own file tools. A `PreToolUse` hook asks you before Read, Edit, Write, Grep or
Glob is given a credential path, so a secret doesn't enter the transcript on its way to being refused by the ledger.
In `claude -p` the ask is a denial. The hook sees only the path. It doesn't cover Bash (`cat .env`) or a search over a
parent folder, and it leaves licensed data alone, which the agent may read to work on.

## What the checks don't catch

- **Page texts written by hand, for an `unread` source.** Without the document, `verify` can only check the folder
  against itself, and a ledger whose pages and hashes were all rewritten agrees with itself. Keep the documents
  where they were recorded from. URLs are not fetched again, so download a document you rely on and record the file.
- **A number from the wrong place.** Numbers are matched by value. A claim can cite a quote that writes the right
  value for a different quantity (0.61 for one method, when the claim is about another).
- **Statements without numbers.** "The method beats the baseline" is recorded on any citation. Only the citation is
  checked.
- **Years and dates.** A label's number (Table 2, Figure 3a, Section 4.1, Eq. 5, § 2) is not checked, but a year
  is, so it has to be among a citation's numbers too.
- **A rigged instrument.** An instrument that prints a number it was given, rather than measuring it, is recorded
  like any other. What the ledger gives you is the path from a claim to the run to the exact code that printed it.
- **Runs outside the ledger.** The agent can run a program directly. That run isn't recorded, so no claim can rest
  on it, but nothing stops it happening.
- **An environment changed in place.** A Python package edited without changing its version is not seen as a change.
  The program's own file and the code snapshot are hashed byte for byte.
- **Reading a holdout directly.** Nothing stops a program on this machine from opening a holdout file without a
  look. The record shows which runs said they used one, and the custody design, labels kept on another node and
  scored there, is not built yet.
- **Runs without a tag.** A run that should count but carries no tag isn't a trial of the plan. `plan_status` lists
  untagged runs of the plan's instruments since the seal, so they are visible, not hidden.
- **Who answers a finding.** Only Claude Code enforces that a persona can't answer findings or edit the work,
  through each subagent's tool list. Elsewhere the ledger can't tell the reviewer from the owner, and the critique
  skill asks for a separate pass.
- **Whether a check really checks.** An answer needs evidence recorded after the critique. Code can't tell whether
  that evidence bears on the finding; the record makes it one step to look.
- **What you record.** `add_source` takes any file, and its text is stored in `.ledger/blobs/`. Look before you
  commit `.ledger/`, and don't commit papers you may not redistribute.
- **What a reproduction shows.** A rerun that matches shows the code gives the same output in the same environment
  on this machine. It does not show the output is right. A run that reads the clock or the network differs for
  reasons that are not errors; declare outputs so the comparison is on the figures, not the log.
- **A declared prior count.** `prior_trials` is the author's word about another ledger, recorded as such. Nothing
  here reads that ledger. Keep it where its head hash can be checked.
- **Wording.** The list is two word families. "Significant" without a test and "outperforms" without a comparison are
  not flagged: the ledger has no entry kind for a test or a comparison to check them against.

- **Memory searches by words.** `recall` and `search_sources` find candidates by shared words; a fact or passage
  worded differently from the query is missed. They check nothing: the probes and `cite` do.
- **A report's references the patterns miss.** `check_report` sees paths, hashes, refs, versions next to a declared
  name and test counts. "All three need updating" names nothing and is `uncheckable`; a claim about behaviour ("the
  tests cover X") is not checked at all.
- **The report checker is experimental.** On the last 20 real subagent reports on this machine it found no wrong
  reference and raised 2 false alarms (a hypothetical path, and a sentence comparing two versions); 10 more could not
  be judged because the worktrees they named were gone. Its pre-set bar for a hook was 3 real catches.
- **Hand-backs are checked when asked.** A `SubagentStop` hook sees the subagent's last message but cannot add to
  the parent's context, so the check is a tool the parent calls (the memory skill says when).
- **Decisions and documents.** A decision does not update the documents that describe the one it replaced.
- **Other MCP clients.** The server is a standard stdio MCP server, but only Claude Code has been tested.

## Data and network use

- **Reads**: the files you pass to `add_source`, `record_draft`, `record_fact` and `check_report` (and the paths a
  checked report names, to see whether they exist), the code folder you pass to `register_instrument`, and
  `~/.claude/plugins/installed_plugins.json` for version checks. Never the files the guard refuses (see Memory).
- **Writes**: only inside the ledger folder (`./.ledger`, or `$LEDGER_DIR`), to the anchor file you name for
  `anchor_ledger`, and to the marked block of the markdown file you name for `write_digest`.
- **Runs**: the instruments you register, when a run tool is called. They do whatever their code does, including
  network access; the server adds none of its own.
- **Network**: the server itself has one destination. `add_source` fetches a URL you give it, one GET to that URL
  with the user agent `research-ledger/0.1`; `annotate_source` with a `doi` sends that DOI to `api.crossref.org`.
Nothing else is sent anywhere: no telemetry, no analytics, no account.
- **Secrets**: an instrument's secrets are read by name from the server's environment and passed to its runs only.
  Their values are never recorded and are masked in stored output. Set them where the server starts, such as the
  `env` of its entry in `.mcp.json`.
- **Installing**: `uv` downloads the server's pinned dependencies from PyPI (`server/uv.lock`) on first run.

## Privacy

The plugin collects nothing. Documents, instruments, runs, quotes and claims stay in the ledger folder on your
machine. The server's only outbound requests are a URL fetch and a DOI lookup you ask for.

## Develop

```bash
cd server && uv run --group dev pytest -q
```

`skills/citation-check/scripts/quotecheck.py` is a copy of `server/src/research_ledger/quotecheck.py`, and a test
fails if they differ. After changing the check, copy it over:
`cp server/src/research_ledger/quotecheck.py skills/citation-check/scripts/`.

## License

Apache License 2.0. See [LICENSE](LICENSE).
