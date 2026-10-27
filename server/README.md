# research-ledger

An MCP server that keeps an append-only, hash-chained ledger of sources, instruments, runs, citations and claims in
a project folder (`.ledger/`). A citation is recorded only when its quote is on the page, or in the run output, it
cites and writes every number it is cited for; a claim only when every number it writes is written in a quote it
cites. An instrument is a program recorded as one exact version of its code and environment, and every run of it is
recorded. `verify` rechecks the whole chain.

Part of the research-integrity plugin; see the repository README for the tools, the data it touches and its network
use.
