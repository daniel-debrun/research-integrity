---
name: plans
description: Decide before you look. Seal what a study will test and how it will be judged before running anything, tag every run with the hypothesis it tests so every trial counts, and spend holdout looks deliberately. Use whenever an experiment, analysis, benchmark or comparison will produce a number that a claim, a decision or a report will rest on, and especially when several variants, seeds or configurations will be tried.
license: Apache-2.0
compatibility: Needs the research-ledger MCP server (the research-integrity plugin). Runs on compute nodes are tagged the same way through research-compute's launch_instrument.
---

# Plans

A result is only as honest as the count of attempts behind it. Forty variants tried and the best one reported reads
exactly like one experiment, unless the attempts are on record. This skill puts them there: the plan is sealed
before the runs, every run says which hypothesis it tests, and every claim records how many trials stand behind it.

1. **Seal the plan first** with `seal_plan`: the question, and each hypothesis with what it claims, what is measured
   and what counts as a result, before any run that tests it. Name the holdout files, if there are any.
2. **Tag every run** with `plan` and `hypothesis` (`run_instrument`, or `launch_instrument` on a node). A tag is set
   when the run starts and never later. Failed runs count too: that's the point.
3. **Claim on what you ran.** A claim citing a tagged run records the count ("1 of 40 trials"). Report that count
   with the result, and say why the reported run is the one you report.
4. **Change the plan only by amendment**, with `amend_plan`: the reason, and what prompted it (a finding, a run). An
   amendment is refused while a blocking finding on the plan is unanswered. Never re-seal under a new name to
   escape a plan's count.
5. **Spend holdout looks deliberately.** `look_holdout` records one look with its reason; pass its id to the single
   run that reads the holdout. Don't read holdout files any other way. Looks are counted, and a failed run still
   spends its look.
6. **Check `plan_status`** before reporting. It lists every trial per hypothesis, the successful runs no claim
   reports, and runs of the same instruments made without a tag since the seal. Account for each: tag them under a
   new hypothesis by amendment, or say in the report why they don't bear on it.
