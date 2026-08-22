#!/usr/bin/env -S uv run
# /// script
# dependencies = ["httpx", "duckdb"]
# ///
"""Fetch OAuth authorization-server metadata for every AS the probes discovered.

READ-ONLY. Records how an arbitrary MCP client could obtain a client_id:

  cimd  -- client_id_metadata_document_supported, the mechanism the MCP draft
           spec says AS and clients SHOULD support
  dcr   -- registration_endpoint (RFC 7591), which that spec marks deprecated
           and retained only for AS that lack CIMD
  none  -- neither: an unattended client cannot get in at all

Nothing here registers a client; that would be a write on someone else's
authorization server.
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
CONCURRENCY, PER_HOST, TIMEOUT = 8, 2, 15.0


def candidates(issuer: str) -> list[str]:
    """RFC 8414 inserts the well-known before the issuer path; plenty of
    deployments do the naive suffix instead, so try both, plus OIDC."""
    u = httpx.URL(issuer)
    path = u.path.rstrip("/")
    base = str(u.copy_with(path="", query=None, fragment=None)).rstrip("/")
    out = [
        f"{base}/.well-known/oauth-authorization-server{path}",
        f"{base}{path}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration{path}",
        f"{base}{path}/.well-known/openid-configuration",
    ]
    return list(dict.fromkeys(out))


async def fetch(c: httpx.AsyncClient, issuer: str) -> dict:
    out = {"issuer": issuer, "fetched_at": dt.datetime.now(dt.UTC).isoformat()}
    for url in candidates(issuer):
        try:
            r = await c.get(url, headers={"accept": "application/json"})
        except Exception as e:
            out.setdefault("errors", []).append(f"{url}: {type(e).__name__}")
            continue
        if r.status_code != 200:
            out.setdefault("tried", []).append([url, r.status_code])
            continue
        try:
            md = r.json()
        except ValueError:
            continue
        if not isinstance(md, dict) or "issuer" not in md:
            continue
        cimd = md.get("client_id_metadata_document_supported")
        dcr = bool(md.get("registration_endpoint"))
        out |= {
            "metadata_url": url,
            "metadata": md,  # kept whole: the draft field names are still moving
            "discovery": "oidc" if "openid-configuration" in url else "rfc8414",
            "advertised_issuer": md.get("issuer"),
            "cimd_supported": bool(cimd),
            "registration_endpoint": md.get("registration_endpoint"),
            "advertises_dcr": dcr,
            # How an unattended MCP client could actually obtain a client_id.
            "client_id_path": "cimd" if cimd else "dcr" if dcr else "preregistered",
            "token_endpoint": md.get("token_endpoint"),
            "grant_types": md.get("grant_types_supported"),
            "pkce": md.get("code_challenge_methods_supported"),
            "iss_param": md.get("authorization_response_iss_parameter_supported"),
            "scopes": md.get("scopes_supported"),
        }
        return out
    out["client_id_path"] = None  # no metadata document found at all
    return out


async def main() -> None:
    # Snapshot from the data, not the clock -- see probe.py for why.
    with duckdb.connect("registry.duckdb", read_only=True) as _db:
        day = _db.sql("SELECT max(snapshot) FROM probes").fetchone()[0]
    out_path = pathlib.Path("data/snapshots") / day / "authservers.jsonl.gz"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if out_path.exists():
        with gzip.open(out_path, "rt") as f:
            done = {json.loads(ln)["issuer"] for ln in f if ln.strip()}

    db = duckdb.connect("registry.duckdb", read_only=True)
    issuers = [i for (i,) in db.sql("""
        SELECT DISTINCT unnest(auth_servers)::VARCHAR FROM probes WHERE auth_servers IS NOT NULL
    """).fetchall()]
    db.close()
    issuers = [i.strip('"') for i in issuers if i and i.strip('"') not in done]
    print(f"{len(issuers)} authorization servers", file=sys.stderr)

    sem, host_sems, lock, n = asyncio.Semaphore(CONCURRENCY), {}, asyncio.Lock(), 0
    with gzip.open(out_path, "at") as f:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"user-agent": UA},
                                     follow_redirects=True) as c:
            async def one(iss: str) -> None:
                nonlocal n
                hs = host_sems.setdefault(httpx.URL(iss).host, asyncio.Semaphore(PER_HOST))
                async with hs, sem:
                    res = await fetch(c, iss)
                async with lock:
                    f.write(json.dumps(res, separators=(",", ":")) + "\n")
                    n += 1
                    if n % 50 == 0:
                        print(f"\r{n}/{len(issuers)}", end="", file=sys.stderr, flush=True)

            await asyncio.gather(*(one(i) for i in issuers))
    print(f"\n{out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
