"""research-compute: compute nodes, and instruments launched on them, recorded in the research ledger.

Nodes are yours, listed in `~/.config/research-compute/nodes.toml` (or $RESEARCH_NODES); this machine is always the
node `local`. A node is reached through a shell: this machine's, or SSH (BatchMode, so a login must not prompt).

```toml
[[node]]
name = "gpu-box"
host = "me@gpu-box"         # or an ~/.ssh/config alias
port = 22                   # optional
workdir = "/scratch/me/research-compute"
# backend = "slurm"         # submit through a Slurm login node; then also account = "...", partitions = ["..."]
# ssh_options = ["-o", "ConnectTimeout=10"]
```

A `node` entry records a node's fingerprint (OS, cores and memory as its limits allow, GPU models, driver, CUDA), and
its version is the hash of it. An instrument registered on a node is fingerprinted there and bound to that node
version. A `launch` entry is recorded before the job starts, so every launch is a trial on the record whether or not
its result is collected; collecting it records its `run`, with outputs hashed on the node and again here.

The server never waits on a job for long: launch returns while the job runs, and wait_run, run_logs and
runs_in_flight reach it again.
"""

from __future__ import annotations

import fnmatch
import io
import json
import math
import os
import shlex
import subprocess
import tarfile
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import instruments as ins
from .quotecheck import Refusal

q = shlex.quote
KEYS = {"name", "host", "port", "backend", "workdir", "account", "partitions", "ssh_options"}
SHA = ('sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d" " -f1; '
       'else shasum -a 256 "$1" | cut -d" " -f1; fi; }')
PROBE = r"""
echo "os=$(uname -srm)"
echo "cores=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN)"
awk '/MemTotal/ {print "mem_kb=" $2}' /proc/meminfo 2>/dev/null
[ -r /sys/fs/cgroup/memory.max ] && echo "mem_limit=$(cat /sys/fs/cgroup/memory.max)"
[ -r /sys/fs/cgroup/cpu.max ] && echo "cpu_max=$(cat /sys/fs/cgroup/cpu.max)"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/gpu=/'
  nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/cuda=\1/p' | head -n 1
fi
"""
LIVE = r"""
[ -r /proc/loadavg ] && echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
awk '/MemAvailable/ {print "mem_free_kb=" $2}' /proc/meminfo 2>/dev/null
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null \
    | sed 's/^/gpu=/'
fi
"""


# --- nodes ----------------------------------------------------------------------------------------------------------
def inventory_path() -> Path:
    return Path(os.environ.get("RESEARCH_NODES") or Path.home() / ".config" / "research-compute" / "nodes.toml")


def inventory() -> dict:
    """The nodes: `local`, and those in the nodes file."""
    nodes = {"local": {"name": "local", "backend": "local"}}
    path = inventory_path()
    if not path.exists():
        return nodes
    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise Refusal(f"{path} could not be read", str(e)[:200], "fix the file; its format is in the plugin's README")
    for n in data.get("node", []):
        name = n.get("name", "")
        unknown = sorted(set(n) - KEYS)
        if unknown:
            raise Refusal(f"node `{name}` in {path} has an unknown key `{unknown[0]}`", f"keys: {', '.join(sorted(KEYS))}")
        if not ins.NAME.fullmatch(name) or name == "local":
            raise Refusal(f"`{name}` in {path} is not a node name", "lowercase letters, digits, '.', '_', '-'; "
                          "`local` is this machine")
        if not n.get("host") or not n.get("workdir"):
            raise Refusal(f"node `{name}` in {path} needs host and workdir")
        backend = n.get("backend", "ssh")
        if backend not in ("ssh", "slurm"):
            raise Refusal(f"node `{name}` has backend `{backend}`", "a backend is ssh or slurm")
        nodes[name] = {**n, "backend": backend}
    return nodes


def node_spec(name: str) -> dict:
    nodes = inventory()
    if name not in nodes:
        raise Refusal(f"no node `{name}`", f"the nodes are {', '.join(nodes)}, from {inventory_path()}",
                      "add it to that file")
    return nodes[name]


class Host:
    """A node reached through a shell. It answers the four questions an instrument's fingerprint asks."""

    def __init__(self, spec: dict):
        self.spec, self.name = spec, spec["name"]

    def argv(self, script: str) -> list:
        return ["sh", "-c", script]

    def sh(self, script: str, input: bytes | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
        try:
            p = subprocess.run(self.argv(script), input=input, capture_output=True, timeout=timeout,
                               stdin=None if input is not None else subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise Refusal(f"{self.name} did not answer within {int(timeout)} s")
        except OSError as e:
            raise Refusal(f"could not reach {self.name}", str(e)[:200])
        if p.returncode == 255 and self.spec["backend"] != "local":
            raise Refusal(f"could not reach {self.name} over SSH", p.stderr.decode("utf-8", "replace")[-300:],
                          f"check that `ssh {self.spec['host']}` logs in without a prompt")
        return p

    def out(self, script: str, **kw) -> str:
        p = self.sh(script, **kw)
        if p.returncode:
            raise Refusal(f"a command on {self.name} failed (exit {p.returncode})",
                          p.stderr.decode("utf-8", "replace")[-300:])
        return p.stdout.decode("utf-8", "replace")

    def which(self, program: str):
        p = self.sh(f"command -v {q(program)}")
        return (p.stdout.decode().strip() or None) if p.returncode == 0 else None

    def realpath(self, path: str) -> str:
        return self.out(f"readlink -f {q(path)} 2>/dev/null || echo {q(path)}").strip()

    def sha_file(self, path: str) -> str:
        return self.out(f"{SHA}; sha {q(path)}").strip()

    def python_listing(self, path: str) -> str:
        return self.out(f"{q(path)} -c {q(ins._LIST_PYTHON)}", timeout=120)


class SSH(Host):
    def argv(self, script: str) -> list:
        s = self.spec
        return ["ssh", "-o", "BatchMode=yes", *(["-p", str(s["port"])] if s.get("port") else []),
                *s.get("ssh_options", []), s["host"], "sh -c " + q(script)]


def host(spec: dict) -> Host:
    return Host(spec) if spec["backend"] == "local" else SSH(spec)


def _fingerprint(h: Host) -> dict:
    kv, gpus = {}, []
    for line in h.out(PROBE, timeout=60).splitlines():
        k, _, v = line.partition("=")
        if k == "gpu":
            idx, name, driver, mem = (x.strip() for x in (v.split(",") + ["", "", "", ""])[:4])
            gpus.append({"index": idx, "name": name, "driver": driver, "memory": mem})
        elif k:
            kv[k] = v.strip()
    cores = int(kv.get("cores") or 0)
    quota, _, period = kv.get("cpu_max", "max").partition(" ")
    if quota.isdigit() and period.isdigit() and int(period):
        cores = min(cores or 10**6, math.ceil(int(quota) / int(period)))
    mem = int(kv.get("mem_kb") or 0) * 1024
    if kv.get("mem_limit", "max").isdigit():
        mem = min(mem or 1 << 62, int(kv["mem_limit"]))
    return {"os": kv.get("os", ""), "cores": cores, "memory_gb": round(mem / 2**30, 1), "gpus": gpus,
            "cuda": kv.get("cuda", "")}


def latest_node(led, name: str):
    return next((e for e in reversed(led.entries()) if e["kind"] == "node" and e["body"]["name"] == name), None)


def register_node(led, name: str) -> dict:
    """Probe a node and record its fingerprint; the same fingerprint again returns the entry already there."""
    spec = node_spec(name)
    fp = _fingerprint(host(spec))
    version = ins._sha(ins._canonical(fp))[:12]
    last = latest_node(led, name)
    if last and last["body"]["version"] == version:
        return {**last, "already": True}
    return led._append("node", {"name": name, "backend": spec["backend"], "fingerprint": fp, "version": version})


def _node_now(led, name: str):
    """The node's registered entry and its host, refused if it was never registered or has changed since."""
    entry = latest_node(led, name)
    if entry is None:
        raise Refusal(f"the node {name} is not registered", "a node is recorded before anything runs on it",
                      f"register it with register_node(\"{name}\")")
    spec = node_spec(name)
    h = host(spec)
    fp = _fingerprint(h)
    if ins._sha(ins._canonical(fp))[:12] != entry["body"]["version"]:
        was = entry["body"]["fingerprint"]
        what = [f"{k}: {was.get(k)} → {fp.get(k)}" for k in fp if fp.get(k) != was.get(k)]
        raise Refusal(f"the node {name} has changed since it was registered", "; ".join(what)[:400],
                      "register_node again, then register the instrument on it again")
    return entry, spec, h


def register_on(led, node: str, name: str, command: str, source: str = "", secrets=(), outputs=(),
                timeout: int = 600) -> dict:
    """Register an instrument on a node: fingerprinted there and bound to the node's current version."""
    entry, _, h = _node_now(led, node)
    return ins.register(led, name, command, source, secrets, outputs, timeout, False, where=h, node=entry)


def _live(h: Host) -> dict:
    kv, gpus = {}, []
    for line in h.out(LIVE, timeout=30).splitlines():
        k, _, v = line.partition("=")
        if k == "gpu":
            idx, util, used, total = (x.strip() for x in (v.split(",") + ["", "", "", ""])[:4])
            gpus.append({"index": idx, "util": util, "memory_used": used, "memory_total": total})
        elif k:
            kv[k] = v.strip()
    return {"load": kv.get("load", ""), "memory_free_gb": round(int(kv.get("mem_free_kb") or 0) / 2**20, 1),
            "gpus": gpus}


def open_launches(led) -> list:
    entries = led.entries()
    ran = {e["body"].get("launch") for e in entries if e["kind"] == "run"}
    return [e for e in entries if e["kind"] == "launch" and e["id"] not in ran]


def list_nodes(led) -> list:
    rows = []
    running = open_launches(led)
    for name, spec in inventory().items():
        last = latest_node(led, name)
        row = {"name": name, "backend": spec["backend"], **({"host": spec["host"]} if spec.get("host") else {}),
               "registered": last["body"]["version"] if last else None,
               "launches_open": [e["id"] for e in running if e["body"]["node"] == name]}
        try:
            row["now"] = _live(host(spec))
        except Refusal as r:
            row["unreachable"] = r.error
        rows.append(row)
    return rows


# --- launching ------------------------------------------------------------------------------------------------------
def _tar(members: list) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.mtime = int(time.time())
            if data is None:
                info.type, info.mode = tarfile.DIRTYPE, mode
                t.addfile(info)
            else:
                info.size, info.mode = len(data), mode
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _workdir(led, spec: dict, h: Host) -> str:
    if spec["backend"] == "local":
        d = (led.root / "work" / "nodes" / "local").resolve()
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    return h.out(f"mkdir -p {q(spec['workdir'])} && cd {q(spec['workdir'])} && pwd").strip()


def _script(b: dict, label: str, rdir: str, args: list, gpu_ids: list, timeout: int) -> str:
    snap = f"{rdir}/snapshot"
    argv = " ".join(q(a.replace(ins.SNAP, snap)) for a in b["argv"]) + "".join(" " + q(a) for a in args)
    env = [f"INSTRUMENT={q(label)}", 'INSTRUMENT_OUT="$PWD"', f"INSTRUMENT_SNAPSHOT={q(snap)}"]
    if b["files"] and any(p["kind"] == "python" for p in b["env"]):
        env.append(f"PYTHONPATH={q(snap)}")
    if gpu_ids:
        env.append(f"CUDA_VISIBLE_DEVICES={q(','.join(gpu_ids))}")
    env += [f'{s}="${s}"' for s in b["secrets"]]
    return f"""#!/bin/sh
cd "$(dirname "$0")" || exit 1
echo $$ > pid
date -u +%Y-%m-%dT%H:%M:%S+00:00 > started
if [ -f secrets.env ]; then set -a; . ./secrets.env; set +a; rm -f secrets.env; fi
cd out
TO=""
if command -v timeout >/dev/null 2>&1; then TO="timeout {int(timeout)}"; fi
env -i PATH="$PATH" HOME="$HOME" ${{LANG:+LANG="$LANG"}} {' '.join(env)} $TO {argv} > ../stdout 2> ../stderr &
echo $! > ../child
wait $!
echo $? > ../exit.tmp
cd ..
date -u +%Y-%m-%dT%H:%M:%S+00:00 > ended
mv exit.tmp exit
"""


def _pick_gpus(led, h: Host, node: str, n: int) -> list:
    gpus = _live(h)["gpus"]
    if not gpus:
        raise Refusal(f"{node} has no GPUs that nvidia-smi reports", "a launch asked for GPUs", "ask for gpus=0")
    held = {g for e in open_launches(led) if e["body"]["node"] == node for g in e["body"].get("gpu_ids", [])}
    free = sorted((g for g in gpus if g["index"] not in held), key=lambda g: float(g["memory_used"] or 0))
    if len(free) < n:
        raise Refusal(f"{node} has {len(free)} GPUs not held by open launches; {n} asked for",
                      f"held: {', '.join(sorted(held)) or 'none'}", "wait for a launch to end, or ask for fewer")
    return [g["index"] for g in free[:n]]


def _state_path(led, launch_id: str) -> Path:
    return led.root / "work" / "launches" / (launch_id.split(":", 1)[1] + ".json")


def _state(led, launch: dict) -> dict:
    p = _state_path(led, launch["id"])
    if not p.exists():
        raise Refusal(f"{launch['id']} has no working state here", f"{p} is missing; the job's folder is unknown",
                      "find it under the node's workdir by the launch's id")
    return json.loads(p.read_text())


def launch(led, ref: str, args=(), gpus: int = 0, cores: int = 0, memory_gb: float = 0, minutes: int = 0,
           follow: float = 20) -> dict:
    """Start a node instrument on `args`. The launch is recorded before the job starts; a launch that fails to start
    is recorded with a failed run."""
    inst = ins.resolve(led, ref)
    b = inst["body"]
    label = f"{b['name']}@{b['version']}"
    if not b.get("node"):
        raise Refusal(f"{label} is not registered on a node", "a launch runs an instrument on the node it was "
                      "registered on", "register it with register_instrument_on, or run it with run_instrument")
    if not all(isinstance(a, str) for a in args):
        raise Refusal("every arg is a string")
    entry, spec, h = _node_now(led, b["node"])
    changed = ins.drift(led, inst, h)
    if changed:
        raise Refusal(f"what {label} runs with on {b['node']} has changed since it was registered",
                      "; ".join(changed), "register it on the node again: that is a new version")
    missing = [s for s in b["secrets"] if not os.environ.get(s)]
    if missing:
        raise Refusal(f"the secret {missing[0]} is not set", "the server passes on only secrets set in its own "
                      "environment", "set it where the server starts (the env of its entry in .mcp.json)")
    timeout = int(minutes * 60) if minutes else b["timeout"]
    gpu_ids = _pick_gpus(led, h, b["node"], gpus) if gpus and spec["backend"] != "slurm" else []
    e = led._append("launch", {"instrument": inst["id"], "ref": label, "node": b["node"], "node_version":
                               entry["body"]["version"], "backend": spec["backend"], "args": list(args),
                               "resources": {"gpus": gpus, "cores": cores, "memory_gb": memory_gb, "minutes": minutes},
                               "gpu_ids": gpu_ids})
    key = e["id"].split(":", 1)[1]
    try:
        rdir = f"{_workdir(led, spec, h)}/{key}"
        members = [("out", None, 0o755), ("snapshot", None, 0o755),
                   ("run.sh", _script(b, label, rdir, list(args), gpu_ids, timeout).encode(), 0o755)]
        members += [(f"snapshot/{rel}", led.blob(sha), 0o555 if rel in b["executable"] else 0o444)
                    for rel, sha in b["files"].items()]
        if b["secrets"]:
            members.append(("secrets.env", "".join(f"{s}={q(os.environ[s])}\n" for s in b["secrets"]).encode(), 0o600))
        h.out(f"mkdir -p {q(rdir)} && tar -C {q(rdir)} -xf -", input=_tar(members))
        job = ""
        if spec["backend"] == "slurm":
            opts = [f"--job-name=rl-{key}", "--output=/dev/null", f"--chdir={q(rdir)}",
                    f"--time={max(1, math.ceil(timeout / 60) + 5)}"]
            opts += [f"--account={q(spec['account'])}"] if spec.get("account") else []
            opts += [f"--partition={q(spec['partitions'][0])}"] if spec.get("partitions") else []
            opts += [f"--gres=gpu:{int(gpus)}"] if gpus else []
            opts += [f"--cpus-per-task={int(cores)}"] if cores else []
            opts += [f"--mem={int(math.ceil(memory_gb))}G"] if memory_gb else []
            job = h.out(f"cd {q(rdir)} && sbatch --parsable {' '.join(opts)} run.sh").strip().split(";")[0]
        else:
            h.out(f"cd {q(rdir)} && if command -v setsid >/dev/null 2>&1; then setsid nohup sh run.sh >/dev/null "
                  f"2>&1 & else nohup sh run.sh >/dev/null 2>&1 & fi")
    except Refusal as r:
        run = _record(led, e, inst, {"exit": None, "error": r.error}, b"", r.meaning.encode(), {})
        raise Refusal(f"{label} could not start on {b['node']}: {r.error}", f"{e['id']} and its failed run "
                      f"{run['id']} are recorded", "fix what the error names and launch again")
    p = _state_path(led, e["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"dir": rdir, "job": job, "node": b["node"]}))
    return wait(led, e["id"], follow)


def _status(led, launch: dict) -> str:
    """running, finished or lost (it ended without recording its exit, or never ran)."""
    st = _state(led, launch)
    h = host(node_spec(launch["body"]["node"]))
    d = q(st["dir"])
    s = h.out(f"cd {d} && if [ -f exit ]; then echo finished; elif [ -f pid ] && kill -0 \"$(cat pid)\" 2>/dev/null; "
              f"then echo running; elif [ -f pid ]; then echo lost; else echo pending; fi").strip()
    if s == "pending" and st.get("job"):
        state = h.out(f"squeue -h -j {q(st['job'])} -o %T 2>/dev/null || true").strip()
        return "running" if state else "lost"
    return "running" if s == "pending" else s


def logs(led, launch_id: str, offset: int = 0, stream: str = "stdout") -> dict:
    launch_e = led.get(launch_id, "launch")
    if stream not in ("stdout", "stderr"):
        raise Refusal(f"`{stream}` is not a stream", "stdout or stderr")
    st = _state(led, launch_e)
    h = host(node_spec(launch_e["body"]["node"]))
    data = h.sh(f"tail -c +{int(offset) + 1} {q(st['dir'] + '/' + stream)} 2>/dev/null | head -c {ins.TAIL_BYTES}")
    text = ins._mask(data.stdout, _secret_values(led, launch_e)).decode("utf-8", "replace")
    return {"ok": True, "launch": launch_id, "stream": stream, "offset": offset, "next": offset + len(data.stdout),
            "text": text}


def _secret_values(led, launch_e: dict) -> dict:
    b = led.get(launch_e["body"]["instrument"], "instrument")["body"]
    return {s: os.environ[s] for s in b["secrets"] if os.environ.get(s)}


def _record(led, launch_e, inst, end: dict, stdout: bytes, stderr: bytes, outputs: dict) -> dict:
    values = _secret_values(led, launch_e)
    code = end.get("exit")
    timed_out = code == 124
    body = {"instrument": inst["id"], "ref": launch_e["body"]["ref"], "args": launch_e["body"]["args"],
            "started": end.get("started", ""), "ended": end.get("ended", ""), "exit": None if timed_out else code,
            "timed_out": timed_out, "stdout": led._put_blob(ins._mask(stdout, values)),
            "stderr": led._put_blob(ins._mask(stderr, values)), "outputs": outputs, "launch": launch_e["id"],
            "node": launch_e["body"]["node"]}
    body.update({k: end[k] for k in ("stopped", "lost", "error") if end.get(k)})
    return led._append("run", body)


def collect(led, launch_e: dict, **flags) -> dict:
    """Bring a finished job's outputs back and record its run: each output hashed on the node and again here."""
    inst = led.get(launch_e["body"]["instrument"], "instrument")
    b = inst["body"]
    st = _state(led, launch_e)
    h = host(node_spec(launch_e["body"]["node"]))
    d = st["dir"]
    head = h.out(f"cd {q(d)} && for f in exit started ended; do printf '%s=' $f; cat $f 2>/dev/null; echo; done")
    end = dict(line.split("=", 1) for line in head.splitlines() if "=" in line)
    code = end.get("exit", "").strip()
    end = {"exit": int(code) if code.lstrip("-").isdigit() else None, "started": end.get("started", "").strip(),
           "ended": end.get("ended", "").strip(), **flags}
    if end["exit"] is None and not flags:
        end["lost"] = True
    stdout = h.sh(f"cat {q(d)}/stdout 2>/dev/null").stdout
    stderr = h.sh(f"cat {q(d)}/stderr 2>/dev/null").stdout
    listing = h.out(f"{SHA}; cd {q(d)}/out && find . -type f | while IFS= read -r f; do "
                    f"printf '%s %s %s\\n' \"$(wc -c < \"$f\" | tr -d ' ')\" \"$(sha \"$f\")\" \"$f\"; done")
    files = {}
    for line in listing.splitlines():
        size, sha, rel = line.split(" ", 2)
        rel = rel[2:] if rel.startswith("./") else rel
        if any(fnmatch.fnmatch(rel, pat) for pat in b["outputs"]):
            files[rel] = (int(size), sha)
    small = [rel for rel, (size, _) in files.items() if size <= ins.KEEP_OUTPUT]
    pulled = {}
    if small:
        data = h.sh(f"cd {q(d)}/out && tar -cf - -- " + " ".join(q(r) for r in small), timeout=600).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            for m in t.getmembers():
                if m.isfile():
                    pulled[m.name[2:] if m.name.startswith("./") else m.name] = t.extractfile(m).read()
    values = _secret_values(led, launch_e)
    outputs = {}
    for rel, (size, sha) in sorted(files.items()):
        if rel not in pulled:
            outputs[rel] = {"sha": sha, "bytes": size, "kept": False}
            continue
        data = pulled[rel]
        if ins._sha(data) != sha:
            raise Refusal(f"{rel} changed on its way from {launch_e['body']['node']}", "its hash there and here differ",
                          "collect it again")
        try:
            kept = ins._mask(data, values).decode("utf-8").encode("utf-8")
            text = True
        except UnicodeDecodeError:
            kept, text = data, False
        outputs[rel] = {"sha": led._put_blob(kept), "bytes": size, "kept": True, "text": text, "node_sha": sha}
    return _record(led, launch_e, inst, end, stdout, stderr, outputs)


def _run_of(led, launch_id: str):
    return next((e for e in led.entries() if e["kind"] == "run" and e["body"].get("launch") == launch_id), None)


def wait(led, launch_id: str, seconds: float = 60) -> dict:
    """Wait up to `seconds` for a launch; when its job has ended, record its run."""
    launch_e = led.get(launch_id, "launch")
    run = _run_of(led, launch_id)
    if run:
        return {"ok": True, "launch": launch_id, "status": "recorded", "run": run}
    deadline = time.monotonic() + max(0.0, min(float(seconds), 600.0))
    while True:
        status = _status(led, launch_e)
        if status in ("finished", "lost"):
            return {"ok": True, "launch": launch_id, "status": "recorded", "run": collect(led, launch_e)}
        if time.monotonic() >= deadline:
            tail = logs(led, launch_id, 0)["text"].splitlines()[-20:]
            return {"ok": True, "launch": launch_id, "status": status, "stdout_tail": "\n".join(tail),
                    "try": "wait_run again, read run_logs, or come back later; the job keeps running"}
        time.sleep(1)


def stop(led, launch_id: str) -> dict:
    """Stop a launch's job and record its run as stopped."""
    launch_e = led.get(launch_id, "launch")
    if _run_of(led, launch_id):
        raise Refusal(f"{launch_id} has already ended and is recorded")
    st = _state(led, launch_e)
    h = host(node_spec(launch_e["body"]["node"]))
    if st.get("job"):
        h.sh(f"scancel {q(st['job'])}")
    else:
        # timeout(1) runs in a process group of its own, so the job's group and its runner's are both signalled
        h.sh(f"cd {q(st['dir'])} && c=$(cat child 2>/dev/null); p=$(cat pid 2>/dev/null); "
             f"[ -n \"$c\" ] && {{ kill -TERM -- -$c 2>/dev/null || kill -TERM $c; }}; "
             f"[ -n \"$p\" ] && {{ kill -TERM -- -$p 2>/dev/null || kill -TERM $p; }}; true")
    for _ in range(10):
        if _status(led, launch_e) != "running":
            break
        time.sleep(0.5)
    return {"ok": True, "launch": launch_id, "status": "stopped", "run": collect(led, launch_e, stopped=True)}


def in_flight(led, check: bool = True) -> list:
    """Every launch not yet recorded as a run, with its status when `check` (which reaches its node)."""
    rows = []
    for e in open_launches(led):
        row = {"launch": e["id"], "ref": e["body"]["ref"], "node": e["body"]["node"], "args": e["body"]["args"],
               "since": e["at"]}
        if check:
            try:
                row["status"] = _status(led, e)
            except Refusal as r:
                row["status"] = f"unknown: {r.error}"
        rows.append(row)
    return rows
