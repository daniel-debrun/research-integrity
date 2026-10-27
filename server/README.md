# research-ledger

An MCP server that keeps an append-only, hash-chained ledger of sources, citations and claims in a project folder
(`.ledger/`). A citation is recorded only when its quote is on the page it cites and writes every number it is cited
for; a claim only when every number it writes is written in a quote it cites. `verify` rechecks the whole chain from
the folder alone.

Part of the research-integrity plugin; see the repository README for the tools, the data it touches and its network
use.
