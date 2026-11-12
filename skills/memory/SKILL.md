---
name: memory
description: Remember facts about the machine and decisions about the work so that a later session, or another agent, can rely on them without being told again, and check a hand-back's file and version claims when they matter. Use whenever you are asked to remember, note or keep something for later that a file, a config value or git can confirm (a version, a commit, a path, a measured count) — prefer it to a memory note, which is never checked again — when a decision is made or reversed, when decisions should go into AGENTS.md or GEMINI.md, and at the start of work that depends on earlier decisions.
license: Apache-2.0
compatibility: Needs the research-ledger MCP server (research-integrity 0.3 or later).
---

# Memory

A memory is only worth keeping if it is still true when it is read. This ledger keeps two kinds, and checks both by
code.

## Facts: probed when written, probed again when recalled

When you are asked to remember something a probe can see, record it as a fact, not a memory note: a note is
never checked again. Record a fact with `record_fact`, naming a probe and its target. The server runs the probe and writes the fact's
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
`firm.status` or `release.split` (an underscore is read as a hyphen). Call `list_subjects` first and use an existing subject when one fits; the refusal
for a new one lists the subjects that look like it. Only pass `new_subject=true` for a question no subject covers.

Reversing a decision is a new decision that `supersedes` the standing one, with the reason it changed. The current
decisions and recent reversals are printed at the start of each session. For agents that read an instruction file
instead (Codex, Gemini CLI), `write_digest("AGENTS.md")` writes them into a marked block; run it after deciding. Never copy
decisions into such a file by hand: a copy without the markers is not replaced by the next `write_digest`, so it
goes stale.

A decision does not update documents that describe the old one. When you supersede a decision, find those documents
and fix them, or say which still need it.

## Hand-back reports: an experimental check

`check_report` checks the paths, commits, branches, tags, versions and test counts a report states, each coming back
`confirmed`, `contradicted` (with what is there), `missing`, `refused`, `ambiguous` or `uncheckable`. It is
experimental: on 20 real subagent reports it found no wrong reference and raised 2 false alarms, so run it when a
report's file, commit or version claims matter to what you do next, not on every report. Check a `contradicted` or
`missing` item yourself before calling it the subagent's error. Say "installed" or "in the repo" when you report a
version: the checker uses those words to pick what to compare against, and a version with no name next to it can't
be checked.

## Finding passages

`search_sources(query)` ranks passages of the recorded sources against a question. It only finds; quote what it
finds with `cite`. When no passage states the number you need, say so instead of writing one.
