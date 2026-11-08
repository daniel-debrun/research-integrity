# research-formal

An optional add-on to the research-integrity plugin. It checks an agent's understanding of a research plan by having
it state the plan as a machine-checked Lean model, and it computes the plan's verdict: success, or needed revision
with the part that failed. It shares the research-ledger Python package, linked here as `server/`.

Tools: `attach_model` and `conclude_plan`, on top of research-ledger's `seal_plan`, `amend_plan` and `plan_status`.
The `formal-plans` skill has the contract and a worked example.

## Install

It is listed in the research-integrity marketplace, next to the core plugin it needs:

```bash
claude plugin marketplace add daniel-debrun/research-integrity
```

```bash
claude plugin install research-formal@research-integrity
```

It needs Lean, installed through [elan](https://lean-lang.org/install). The server looks for `lean` on `PATH` and in
`~/.elan/bin`.

## What is checked

`attach_model` runs in a recorded instrument run, before any trial of the hypothesis. It refuses a model that doesn't
build, where anything rests on `sorry` or an added axiom, whose `design` theorem doesn't take the premises, whose
premises have no example satisfying them, or whose success condition doesn't pass its passing example and fail its
failing one. Every premise needs a citation id, or is marked assumed.

`conclude_plan` takes each measured value from a citation of the hypothesis's own runs, refuses a value the quote
doesn't write, and has Lean compute the verdict in a recorded run. A claim resting on those runs records the verdict.
`verify` rechecks all of it, and lists hypotheses whose latest verdict was revision and whose plan hasn't changed.

## What the checks don't catch

- **A statement that means something else.** A definition can be wrong and still compile. `attach_model` returns each
  premise beside its quote for review, and a model can be critiqued like a draft.
- **False premises.** The proof shows that the conclusion follows from them, not that they hold.
- **A trivial `design`.** Code only checks that it takes the premises, not that it says anything useful.
- **Mathlib projects are untested.** A model folder with a `lakefile` and its own `.lake/packages` should build, but
  only core-Lean models have been run.

The verdict is computed by Lean's evaluator (`#eval`), because the kernel doesn't reduce `Rat` comparisons. Proofs are
checked by the kernel.

## Data and network use

The server reads the model folder you pass and writes only inside the ledger folder. Lean may download the toolchain
named in `lean-toolchain` from `releases.lean-lang.org`. Nothing else is sent anywhere.

## License

Apache License 2.0. See the repository's LICENSE.
