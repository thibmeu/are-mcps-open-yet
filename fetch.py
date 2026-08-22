#!/usr/bin/env -S uv run
# /// script
# dependencies = ["httpx"]
# ///
"""Snapshot the MCP registry to data/snapshots/<date>/servers.jsonl.gz (verbatim payloads)."""
import datetime as dt
import gzip
import json
import pathlib
import sys

import httpx

BASE = "https://registry.modelcontextprotocol.io/v0/servers"
UA = "mcp-registry-analysis/0.1 (+https://github.com/thibmeu; research)"


def main(version: str = "latest") -> None:
    day = dt.date.today().isoformat()
    out = pathlib.Path("data/snapshots") / day / "servers.jsonl.gz"
    out.parent.mkdir(parents=True, exist_ok=True)

    n, cursor = 0, None
    with gzip.open(out, "wt") as f, httpx.Client(timeout=30, headers={"user-agent": UA}) as c:
        while True:
            params = {"limit": 100, "version": version}
            if cursor:
                params["cursor"] = cursor
            r = c.get(BASE, params=params)
            r.raise_for_status()
            page = r.json()
            for row in page["servers"]:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
                n += 1
            cursor = page["metadata"].get("nextCursor")
            print(f"\r{n} servers", end="", file=sys.stderr, flush=True)
            if not cursor:
                break
    print(f"\n{out} ({out.stat().st_size / 1e6:.1f} MB)", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
