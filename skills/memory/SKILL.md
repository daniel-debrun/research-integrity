---
name: memory
description: Remember facts about the machine and decisions about the work so that a later session, or another agent, can rely on them without being told again, and check an agent's hand-back before relying on it. Use when you learn something that will matter later (a version, a commit, a path, a measured count), when a decision is made or reversed, at the start of work that depends on earlier decisions, and on every subagent report that names files, commits, versions or test counts.
license: Apache-2.0
compatibility: Needs the research-ledger MCP server (research-integrity 0.3 or later).
---

# Memory

A memory is only worth keeping if it is still true when it is read. This ledger keeps two kinds, and checks both by
code.

## Facts: probed when written, probed again when recalled

Record a fact with `record_fact`, naming a probe and its target. The server runs the probe and writes the fact's
statement from what it saw; you never supply the value.

| Probe | Use for | Example |
|---|---|---|
| `file` | a file exists, at this exact content | `record_fact("file", "data/test.parquet")` |
| `text_in_file` | a line is in a file (exact, never cut inside a word or number) | `text="ESC_MIN 1206"` |
| `field` | a value in a JSON or TOML file | `key="version"` on `plugin.json` |
| `git_ref` | what a branch or tag points to, whether a commit is in it | `ref="v0.2.1", ancestor_of="main"` |
| `run` | a number an instrument printed (cite it with `cite_run` first) | target: the citation id |

Use `recall(query)` before acting on something you remember. Each fact comes back `current`, `stale` (with what the
probe sees now), `unreachable` or, for a run, `as_of` its date (`rerun=true` runs it again). Act on `stale` as news:
the world changed. Never restate a stale fact as true.

Credentials, keys, other ledgers and folders listed as licensed data are never probed. Don't try another path to
the same file.

## Decisions: one per subject, each naming what it replaced

Record a decision with `decide(subject, question, choice, reason, evidence)`. The subject is a key such as
`firm.status` or `release.split`. Call `list_subjects` first and use an existing subject when one fits; the refusal
for a new one lists the subjects that look like it. Only pass `new_subject=true` for a question no subject covers.

Reversing a decision is a new decision that `supersedes` the standing one, with the reason it changed. The current
decisions and recent reversals are printed at the start of each session. For agents that read an instruction file
instead (Codex, Gemini CLI), `write_digest("AGENTS.md")` writes them into a marked block; run it after deciding.

A decision does not update documents that describe the old one. When you supersede a decision, find those documents
and fix them, or say which still need it.

## Hand-back reports: check before relying

Run `check_report` on every subagent report that names files, commits, branches, tags, versions or test counts,
before you repeat any of it. Each reference comes back `confirmed`, `contradicted` (with what is there), `missing`,
`refused`, `ambiguous` or `uncheckable`. Treat contradicted and missing items as the subagent's errors: check them
yourself and correct them in what you report. Say "installed", "in the repo" and the like when you report versions:
the checker uses those words to pick what to compare against, and a version with no name next to it can't be
checked.

## Finding passages

`search_sources(query)` ranks passages of the recorded sources against a question. It only finds; quote what it
finds with `cite`. When no passage states the number you need, say so instead of writing one.
