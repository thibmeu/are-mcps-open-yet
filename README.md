# MCP registry analysis

This repository contains the data and code used to probe remote servers in the
[official Model Context Protocol registry](https://registry.modelcontextprotocol.io/).
The experiment asks a narrow question: how far can an MCP client get without an
account or credentials?

The included snapshot was collected from France on August 13, 2026. It contains
21,423 latest-version registry entries and 11,121 distinct remote endpoints that
were probed.

## Results in the included snapshot

| Observed posture | Endpoints | Share |
|:--|--:|--:|
| Simply open | 3,511 | 31.6% |
| Authentication required | 3,092 | 27.8% |
| Authentication advertised | 2,566 | 23.1% |
| Broken or unreachable | 1,718 | 15.4% |
| Payment required | 118 | 1.1% |
| Partial response | 92 | 0.8% |
| Throttled | 24 | 0.2% |

"Simply open" means that `initialize` and `tools/list` succeeded without OAuth
being advertised. "Authentication required" means the endpoint challenged for
credentials before exposing its tools. "Authentication advertised" means OAuth
appeared during the exchange while anonymous discovery could still proceed.

The probe never calls a tool. These labels describe tool discovery, not whether
individual tools can be invoked anonymously or why an operator chose a particular
access policy.

## Repository layout

```text
data/snapshots/    Compressed newline-delimited JSON captured during collection
data/site/         Aggregates exported for the article and generated SVG panels
build.sql          Registry snapshot to normalized DuckDB tables
probes.sql         Probe results and derived access-posture views
authservers.sql    Authorization-server metadata tables
labels.sql         Model-assisted capability labels
export.sql         Small aggregate datasets used by the diagrams
fetch.py           Fetch the official registry
probe.py           Run initialize and tools/list without credentials
authservers.py     Fetch public OAuth authorization-server metadata
classify.py        Classify server capabilities using the Claude CLI
render.py          Render aggregate JSON as SVG
```

`registry.duckdb` is derived output and is intentionally ignored by Git. The
compressed files under `data/snapshots/` are the source of truth.

## Requirements

- [`uv`](https://docs.astral.sh/uv/)
- `make`
- The [`claude`](https://docs.anthropic.com/en/docs/claude-code) CLI only if you
  want to classify a new snapshot or replace the included labels

The scripts use inline dependency declarations, so no project environment needs
to be created manually.

## Regenerate the SVG figures

The repository includes the raw registry snapshot, probe results, authorization
metadata, and classification labels used for the article. Regenerate every SVG
from those saved files with:

```sh
make figures
```

This recreates `registry.duckdb`, exports the aggregate JSON files under
`data/site/`, and writes ten figures to `data/site/svg/`. It does not fetch from
the registry, contact any probed server, call Claude, or build a website.

Open the database in an interactive Python session:

```sh
make sql
```

For example, this query produces the endpoint and host counts used by the access
posture diagram:

```sql
SELECT
    posture,
    count(*) AS endpoints,
    count(DISTINCT host) AS hosts
FROM endpoint_auth
GROUP BY posture
ORDER BY endpoints DESC;
```

## Collect a new snapshot

The full probe sends requests to every distinct remote endpoint in the registry.
Review `probe.py`, its concurrency settings, and its request behavior before
running it. The probe sends `initialize`, `notifications/initialized`, and
`tools/list`; it never sends `tools/call` and never supplies credentials.

```sh
make fetch
make build
make probe
make build
make authservers
make build
```

`probe.py` is resumable and accepts an optional limit for a smaller run:

```sh
./probe.py 100
```

At this point you can regenerate every figure except the sensitivity figure. To
create sensitivity labels for the new snapshot, run the optional classification
step, then rebuild the figures:

```sh
make classify
make figures
```

## How classification works

`classify.py` assigns each server two labels: a topic and the highest-sensitivity
capability suggested by its metadata. It currently invokes the authenticated
Claude CLI with the Haiku model. It does not require a separate Anthropic API key,
but it does require a working Claude CLI login.

Claude is not required to reproduce the included results. The generated labels
are saved in `data/snapshots/2026-08-13/labels.jsonl.gz`, and `make figures` reads
that file through DuckDB.

For a new snapshot, reachable servers are classified from registry metadata and
tool names. Servers that require authentication do not expose their tool list, so
their labels use only the name and description in the registry. The sensitivity
cross-tab is therefore exploratory, even after manual spot checks.

The rest of the analysis does not depend on those labels. If you do not have
Claude, you can skip `make classify` and use the access-posture, concentration,
protocol, and authorization figures. You can also replace `classify.py` with
another classifier that writes the same newline-delimited JSON fields:

```json
{"snapshot":"2026-08-13","name":"server/name","topic":"search-web","sensitivity":"public","labelled_at":"2026-08-13T12:00:00Z"}
```

Allowed sensitivity values are `public`, `user-private`, `financial`, and
`infrastructure-control`. See `classify.py` for the topic values and full prompt.

The SVGs contain semantic CSS classes rather than inline colors, so a consuming
page can provide its own light and dark palettes.
