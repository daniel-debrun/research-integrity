# research-compute

An optional add-on to the research-integrity plugin. It registers compute nodes (this machine, hosts reached over
SSH, Slurm clusters through a login node) and launches registered instruments on them. Every launch is recorded in
the same research ledger, before its job starts, and its outputs come back hashed. It shares the research-ledger
Python package, linked here as `server/`.

Tools: `list_nodes`, `register_node`, `register_instrument_on`, `launch_instrument`, `wait_run`, `run_logs`,
`stop_run` (destructive) and `runs_in_flight`. A `SessionStart` hook lists the launches not yet recorded as runs,
reading only the ledger.

## Install

It is listed in the research-integrity marketplace, next to the core plugin it needs:

```bash
claude plugin marketplace add daniel-debrun/research-integrity
```

```bash
claude plugin install research-compute@research-integrity
```

## Nodes

This machine is always the node `local`. Other nodes are yours, in `~/.config/research-compute/nodes.toml` (or the
file `$RESEARCH_NODES` names):

```toml
[[node]]
name = "gpu-box"
host = "me@gpu-box"                      # or an ~/.ssh/config alias
port = 22                                # optional
workdir = "/scratch/me/research-compute" # where launches get a folder each
# ssh_options = ["-o", "ConnectTimeout=10"]

[[node]]
name = "cluster"
backend = "slurm"                        # jobs go through sbatch on the login node
host = "cluster-login"
workdir = "/scratch/me/research-compute" # a filesystem the compute nodes share
account = "my-account"
partitions = ["gpu"]
```

SSH logins must work without a prompt: a key, or an agent. The server adds `BatchMode=yes`.

## What it does on a node

- **Probing:** `register_node` and every launch run a short shell probe: `uname`, `nproc`, `/proc/meminfo`, the cgroup
  limits, and `nvidia-smi`.
- **Copying:** a launch copies the instrument's registered code, a `run.sh`, and, if the instrument has secrets, a
  `secrets.env` readable only by you, which `run.sh` deletes as it starts. These go to `<workdir>/<launch id>/` as a
  tar stream over SSH.
- **Running:** the job runs with `nohup` (or `sbatch`) in a minimal environment. Its outputs stay there: the ones that
  match the instrument's patterns and are under 20 MB are copied back when the run is recorded, and the rest are hashed
  where they are.
- **What isn't enforced:** on SSH nodes, `cores` and `memory_gb` are recorded, not enforced. On Slurm they go to
  `sbatch`. On a Slurm node the fingerprint is the login node's, not the compute node's.

## Data and network use

The server connects only to the hosts in your nodes file, with your `ssh`. It sends the instrument's code, its args
and its declared secrets, and it reads back status, logs and outputs. Nothing else is sent anywhere.

## License

Apache License 2.0. See the repository's LICENSE.
