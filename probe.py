#!/usr/bin/env -S uv run
# /// script
# dependencies = ["httpx", "duckdb"]
# ///
"""Measure anonymous gate depth for each remote endpoint in registry.duckdb.

READ-ONLY BY CONSTRUCTION: sends `initialize` and `tools/list` only, never
`tools/call`, never credentials, never paths beyond the RFC 9728 well-knowns.
Resumable -- re-running skips URLs already in the output file.

  ./probe.py [limit]     probe endpoints not yet in today's output
  ./probe.py --retry     re-probe only transient failures, slower and gentler
"""
import asyncio
import datetime as dt
import gzip
import json
import pathlib
import sys

import duckdb
import httpx

UA = "mcp-registry-analysis/0.1 (+https://github.com/thibmeu; research)"
PROTOCOL = "2025-06-18"
CONCURRENCY = 8
PER_HOST = 2  # gateway.pipeworx.io alone owns 1312 of the 11k URLs
TIMEOUT = 15.0

# Gate depth: how far an anonymous client gets.
UNREACHABLE, GATED_TRANSPORT, GATED_TOOLS, OPEN = 0, 1, 2, 3


def parse_body(r: httpx.Response) -> dict | None:
    """MCP replies with JSON or a single-event SSE stream."""
    text = r.text[:200_000]
    if "text/event-stream" in r.headers.get("content-type", ""):
        text = "".join(ln[5:] for ln in text.splitlines() if ln.startswith("data:"))
    try:
        return json.loads(text)
    except ValueError:
        return None


async def probe(c: httpx.AsyncClient, url: str) -> dict:
    out = {"url": url, "probed_at": dt.datetime.now(dt.UTC).isoformat(), "depth": UNREACHABLE}
    hdrs = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROTOCOL, "capabilities": {},
        "clientInfo": {"name": "mcp-registry-analysis", "version": "0.1"}}}
    try:
        r = await c.post(url, json=rpc, headers=hdrs)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:300]
        return out

    out["init_status"] = r.status_code
    out["www_authenticate"] = r.headers.get("www-authenticate")
    out["content_type"] = r.headers.get("content-type")
    body = parse_body(r)

    if r.status_code in (401, 403):
        out["depth"] = GATED_TRANSPORT
    elif r.status_code < 300 and body and "result" in body:
        out["server_info"] = body["result"].get("serverInfo")
        out["capabilities"] = list(body["result"].get("capabilities") or {})
        out["negotiated_protocol"] = body["result"].get("protocolVersion")
        out["depth"] = GATED_TOOLS  # initialize passed; tools/list decides 2 vs 3
        hdrs2 = dict(hdrs, **{"mcp-protocol-version": out["negotiated_protocol"] or PROTOCOL})
        if sid := r.headers.get("mcp-session-id"):
            hdrs2["mcp-session-id"] = sid
        try:
            await c.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                         headers=hdrs2)
            r2 = await c.post(url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                              headers=hdrs2)
            out["tools_status"] = r2.status_code
            b2 = parse_body(r2)
            if r2.status_code < 300 and b2 and "result" in b2:
                tools = b2["result"].get("tools") or []
                out["depth"] = OPEN
                out["n_tools"] = len(tools)
                out["tool_names"] = [t.get("name") for t in tools][:200]
        except Exception as e:
            out["tools_error"] = f"{type(e).__name__}: {e}"[:300]
    else:
        out["error"] = f"non-mcp response {r.status_code}"
        out["body_snippet"] = r.text[:300]

    # RFC 9728 protected-resource metadata -- present on servers that advertise
    # OAuth whether or not they actually gate anonymous access.
    prm_url = None
    if wa := out["www_authenticate"]:
        for part in wa.split(","):
            if "resource_metadata" in part:
                prm_url = part.split("=", 1)[1].strip().strip('"')
    if not prm_url:
        u = httpx.URL(url)
        prm_url = str(u.copy_with(path="/.well-known/oauth-protected-resource",
                                  query=None, fragment=None))
    try:
        rp = await c.get(prm_url, headers={"accept": "application/json"})
        out["prm_status"] = rp.status_code
        if rp.status_code < 300 and (prm := parse_body(rp)):
            out["prm"] = prm
            out["auth_servers"] = prm.get("authorization_servers")
            out["scopes"] = prm.get("scopes_supported")
    except Exception as e:
        out["prm_error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def transient(row: dict) -> bool:
    """Worth another attempt. 404/405 and non-MCP 200s are settled answers."""
    return row.get("init_status") in (None, 429, 500, 502, 503, 504) and row["depth"] == 0


async def main(arg: str | None = None) -> None:
    global CONCURRENCY, PER_HOST, TIMEOUT
    retry = arg == "--retry"
    limit = None if retry else arg
    # Snapshot comes from the data, not the clock: a resume after midnight would
    # otherwise start a fresh file, see nothing done, and re-probe all 11k
    # third-party endpoints.
    with duckdb.connect("registry.duckdb", read_only=True) as _db:
        day = _db.sql("SELECT max(snapshot) FROM endpoints").fetchone()[0]
    out_path = pathlib.Path("data/snapshots") / day / "probes.jsonl.gz"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen: dict[str, dict] = {}
    if out_path.exists():
        with gzip.open(out_path, "rt") as f:
            for ln in f:
                if ln.strip():
                    r = json.loads(ln)
                    seen[r["url"]] = r  # later rows win; probes.sql dedupes the same way

    if retry:
        # Slower and one-at-a-time per host: several of these failed *because*
        # we were too quick the first time.
        CONCURRENCY, PER_HOST, TIMEOUT = 4, 1, 30.0
        retry_urls = [u for u, r in seen.items() if transient(r)]
        print(f"retrying {len(retry_urls)} transient failures", file=sys.stderr)
        await run(retry_urls, out_path)
        return

    done = set(seen)
    db = duckdb.connect("registry.duckdb", read_only=True)
    # Round-robin across domains. Politeness is the per-host semaphore's job;
    # this is for throughput -- it spreads the one host that owns 1312 URLs
    # across the whole run instead of leaving a 2-at-a-time tail at the end.
    urls = [u for (u,) in db.sql("""
        SELECT url FROM endpoints WHERE NOT url_templated
        ORDER BY row_number() OVER (PARTITION BY domain ORDER BY n_servers DESC, url),
                 n_servers DESC, url
    """).fetchall() if u not in done]
    db.close()  # don't hold DuckDB's read lock for the whole run; make build needs it
    if limit:
        urls = urls[: int(limit)]
    print(f"{len(urls)} to probe ({len(done)} already done)", file=sys.stderr)

    await run(urls, out_path)


async def run(urls: list[str], out_path: pathlib.Path) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    host_sems: dict[str, asyncio.Semaphore] = {}
    lock = asyncio.Lock()
    n = 0
    limits = httpx.Limits(max_connections=CONCURRENCY * 2)
    with gzip.open(out_path, "at") as f:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, headers={"user-agent": UA}, follow_redirects=True, limits=limits
        ) as c:
            async def one(url: str) -> None:
                nonlocal n
                host = httpx.URL(url).host
                hs = host_sems.setdefault(host, asyncio.Semaphore(PER_HOST))
                # Host gate before the global one: a task waiting on a busy host
                # must not sit on one of the 8 global slots doing nothing.
                async with hs, sem:
                    res = await probe(c, url)
                async with lock:
                    f.write(json.dumps(res, separators=(",", ":")) + "\n")
                    f.flush()  # else a long run shows no progress and a kill loses the buffer
                    n += 1
                    if n % 25 == 0:
                        print(f"\r{n}/{len(urls)}", end="", file=sys.stderr, flush=True)

            await asyncio.gather(*(one(u) for u in urls))
    print(f"\n{out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
