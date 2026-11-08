---
name: formal-plans
description: State a research plan's understanding as a machine-checked Lean model before running anything: the quantities, the premises with their sources, parameters derived rather than picked, and a success condition that can be computed. The runs then reach a computed verdict of success or needed revision. Use when a plan has quantities to derive or measure and getting it wrong would be costly, and when asked to formalise an approach.
license: Apache-2.0
compatibility: Needs the research-formal and research-ledger MCP servers, and Lean installed through elan. The bundled example uses core Lean only.
---

# Formal plans

Prose can sound like understanding without being it. Here the plan's understanding has to survive a proof checker,
and its result is computed rather than argued. This doesn't verify code, and Lean is only the means: you are checking
whether you understand the plan.

## The loop

1. **Seal the plan** with `seal_plan`.
2. **Write the model**: a folder holding `Plan.lean` and `lean-toolchain`. `example/` is a complete one to start from.
3. **Attach it** with `attach_model`, before any run of the hypothesis, mapping each premise to a citation id or
   `"assumed"`. Read what it returns: each premise beside its quote. Check that they mean the same thing, because
   nothing else will.
4. **Run the trials**, tagged to the hypothesis, and cite their output with `cite_run`.
5. **Conclude** with `conclude_plan`, giving each measured value with the citation that writes it.
6. **Act on the verdict.** On `revision`, amend the plan with `amend_plan`, passing the verdict id in `because`, and
   state which test failed. Don't re-run until it passes, don't reinterpret the numbers, and don't weaken a test
   after seeing a result without saying so in the amendment.

## The model

One namespace per hypothesis, named by its id (`H1`), declaring:

| Name | What it says |
|---|---|
| `World` | The quantities the plan reasons about |
| `Premises (w : World) : Prop` | What must be true, one field per premise. An outside fact, such as a statistical bound, goes here as a premise with its citation; you don't have to prove it |
| `model`, `model_ok : Premises model` | A world where the premises hold, which shows they don't contradict each other |
| `design` | A theorem taking `w` and `Premises w`, concluding what the plan relies on, such as why a parameter has its value |
| `Measured` | What a run reports, with `Nat`, `Int` or `Rat` fields |
| `checks` | The success condition as named tests, `List (String × (Measured → Bool))` |
| `passing`, `failing` | One `Measured` that meets the condition and one that doesn't |

## What a good model does

- **Derive parameters.** If a sample size or threshold matters, `design` should conclude why it has that value. A
  `design` proving something trivial passes the checks and fails the point.
- **Name the failure.** Split the condition into tests, so a verdict can say which part failed.
- **Prefer premises to axioms.** `axiom` and `sorry` are refused. Anything you can't prove becomes a visible premise.
- **Use whole-number units in derivations.** Core Lean's `omega` handles `Nat`. Basis points work well. `Rat` is fine
  in `checks`, where values are computed, not proved.

## What this doesn't check

Lean proves that the conclusion follows from the premises. It doesn't show that the premises are true, or that the
Lean statement means what its source means. That's the reviewer's job: a model can be critiqued like a draft
(`record_critique` on its id), and the verdict is computed by Lean's evaluator, not proved in its kernel.
