---
name: critique
description: How a critique is recorded and answered so that a change of direction is traceable and every point is checked by the work's owner, not taken on the reviewer's word. Use when a draft, plan, claim or result is reviewed (an adversarial review, a peer review, a teammate's comments) and whenever acting on review findings.
license: Apache-2.0
compatibility: The record needs the research-ledger MCP server. The persona subagents (adversary, reviewer) are Claude Code's; elsewhere, run the review inline, as below.
---

# Critique

A review often changes a settled plan, result or draft, and reviewers are wrong too. This skill makes both visible:
the target is recorded at an exact version, the reviewer's findings are recorded with their evidence, and each finding
is answered by the work's owner with evidence of their own before the work moves on.

## The loop

1. **Record the target** at its exact version: `record_draft` for a file, or use a claim, run or citation id.
   Quote a draft by its id (`cite(source=<draft id>, page=1)`), not by adding its file as a source.
2. **Review it.** In Claude Code, hand it to the `adversary` or `reviewer` subagent with only the target and the files
   it rests on, not your reasoning. Elsewhere, review it yourself in a separate pass, as if you hadn't written it. The
   reviewer records its findings with `record_critique`: each one says what is wrong and where, whether it is
   **blocking** (it would change the conclusion or the plan), and the evidence it recorded.
3. **Check every finding yourself.** Read what it points to, rerun what matters, and record that check as a citation
   or a run. A reviewer's number is a claim like any other.
4. **Answer each one** with `answer_finding`: `upheld` or `dismissed`, a reason, and your evidence. A blocking finding
   is refused without evidence recorded after the critique, whichever way you answer it.
5. **Revise.** Record the new version with `record_draft(path, supersedes=...)`, or a corrected claim with
   `claim(..., supersedes=...)`. Both are refused while a blocking finding on the old version is unanswered.
6. **Verify.** `verify` reports an upheld finding whose draft or claim still stands, and a claim still resting on a
   run or citation that a finding was upheld against.

## Wording

`record_draft` answers with `wording`: each use of a word the draft may not use on its own authority ("novel",
"first to", "unprecedented"; "proves", "demonstrates conclusively"), with its line and whether it sits in quoted text.
The draft is recorded all the same. The reviewer makes a finding of each unquoted use, non-blocking unless the word
carries the conclusion; a quoted use is someone else's word and needs none. `claim` answers the same way for a
statement that uses one.

## Reporting

Never pass a review on as it stands. Report each finding with three parts: what the reviewer said, what you found
when you checked, and your verdict. Say plainly where the reviewer was wrong.
