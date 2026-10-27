---
name: remote-runs
description: Run a registered instrument on a compute node (a GPU box over SSH, a Slurm cluster, or this machine with chosen GPUs) so that every launch is on the research ledger before it starts and its outputs come back hashed. Use when a measurement needs more compute than the session's machine, or runs longer than a tool call.
license: Apache-2.0
compatibility: Needs the research-compute and research-ledger MCP servers (the research-compute and research-integrity plugins), and SSH logins that don't prompt for the nodes you list.
---

# Remote runs

A run on another machine is still a measurement, so it's recorded the same way as a local one. It also outlives the
tool call that starts it. The launch is recorded before the job starts, so a launch you never collect still counts.

1. **Know the nodes.** `list_nodes` shows `local` and the nodes in `~/.config/research-compute/nodes.toml`, with what
   each has free now. The person lists nodes; don't add hosts to that file yourself unless asked.
2. **Register the node** with `register_node`, once, and again whenever it changes. A launch is refused on a node
   whose fingerprint (OS, cores, memory, GPUs, driver, CUDA) differs from its registered one.
3. **Register the instrument on the node** with `register_instrument_on`. It's fingerprinted there: the Python and
   packages on the node, not here.
4. **Launch it** with `launch_instrument`, giving the resources it needs (`gpus`, `cores`, `memory_gb`, `minutes`).
   It returns while the job runs, or with the run if the job ended within `follow_seconds`.
5. **Come back to it.** `wait_run` waits up to 10 minutes and records the run when the job ends. `run_logs` reads
   output so far, and `runs_in_flight` lists every launch not yet recorded. For a long job, schedule your own return
   (in Claude Code, `/loop` or a scheduled task) rather than waiting in a tool call.
6. **Cite the output** with `cite_run`, as for a local run.

Launch once per configuration you mean to measure. Every launch is a trial on the record: one you stop with
`stop_run`, or never collect, still counts.
