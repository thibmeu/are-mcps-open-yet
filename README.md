# Are MCPs open yet?

This repository contains the data and code used to measure how many remote
servers in the official MCP registry expose `tools/list` without credentials.

## Reproduce the result

Install [`uv`](https://docs.astral.sh/uv/) and `make`, then run:

```sh
make result
```

Expected output for the included snapshot:

```text
3511 of 11121 remote endpoints (31.6%) expose tools/list anonymously
```

This rebuilds `registry.duckdb` from the files in
`data/snapshots/2026-08-13/`. It does not use the network or call a model. To
select a snapshot explicitly, run `make result SNAPSHOT=2026-08-13`.

The probe only sends `initialize`, `notifications/initialized`, and
`tools/list`. It never calls a tool or sends credentials. "Open" therefore
means that tool descriptions are visible, not that the tools themselves can be
used anonymously.

## Use the data

Run `make sql` to open the generated DuckDB database. The raw, compressed JSONL
files under `data/snapshots/` are the source data. The SQL files build the tables
and derived access categories.

Run `make figures` to rebuild the aggregate JSON and SVG files in `data/site/`.

## Collect a new snapshot

```sh
make fetch
make build
make probe
make build
make authservers
make build
make classify  # optional; requires a logged-in Claude CLI
make figures
```

The probe contacts every distinct remote endpoint in the registry. Pass a limit
for a smaller run with `./probe.py 100`. Collection is resumable.

## License

MIT
