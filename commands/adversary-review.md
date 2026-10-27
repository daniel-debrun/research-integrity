---
description: Adversarial review of a draft, claim or result, recorded in the research ledger, with every finding checked and answered by you
argument-hint: <draft file, or a ledger id> [files and logs it rests on]
---

Run an adversarial review of: $ARGUMENTS

1. **Record the target.** If it is a file, record it with `record_draft` (pass `supersedes` if an earlier version of
   it is in the ledger), and note the numbers it lists as untraced. If it is a ledger id, use that.
2. **Hand it to the adversary** subagent with only what it needs: the target's id, the paths of the files, code and
   logs it rests on, and what it may run. Don't pass your reasoning, your conclusions or what you expect it to find.
3. **When it returns, check every finding yourself** (`list_findings` on the target). The persona can be wrong in
   both directions, so don't relay its findings as settled:
   - read the passage, log or code it points to;
   - rerun what it ran if the finding is blocking;
   - record your check (add_source and cite, or run an instrument and cite_run).
4. **Answer each finding** with `answer_finding`: upheld or dismissed, a reason, and the evidence you just recorded.
   A blocking finding can't be answered without it.
5. **Report to the user** finding by finding: what the adversary said, what you found when you checked, and your
   verdict, including where the adversary was wrong. List what still needs doing before the target can be acted on.
   Don't revise the draft unless the request above asks you to; when it does, revise it and record the new version
   with `record_draft(path, supersedes=<old id>)`, then run `verify`.
