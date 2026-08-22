#!/usr/bin/env -S uv run
# /// script
# dependencies = ["duckdb"]
# ///
"""Label reachable remote servers with a topic and a data-sensitivity level.

Shells out to the `claude` CLI, so this needs no ANTHROPIC_API_KEY -- it reuses
whatever credentials Claude Code already has.

Two independent axes. `topic` describes what the server is for; `sensitivity`
describes the most sensitive capability indicated by its metadata or tool list.
The cross-tab compares those labels with anonymous access posture.

BATCH is 100 because each invocation re-sends ~23k tokens of Claude Code system
prompt regardless of payload; at 20/batch that overhead dominates and costs 4x more.

  ./classify.py [limit]
"""
import asyncio
import datetime as dt
import gzip
import json
import pathlib
import re
import sys

import duckdb

MODEL = "haiku"
# .3 sharpens capability-vs-topic: hand-checking 30 labels found the model reading
# domain nouns ("crypto", "treasury") as `financial` even when every tool was
# read-only. The error is directional and inflates the two buckets the analysis
# leans on, so those get re-labelled with `--relabel`; newest wins in labels.sql.
PROMPT_VERSION = "2026-08-13.3-cli"
SUSPECT = ("financial", "infrastructure-control")
BATCH = 100
# 6 sustained heavy invocations trips a rate limit (exit 1, empty stderr) and lost
# 29/60 batches; 3 runs reliably. Retry twice, then surface the failure.
CONCURRENCY = 3
RETRIES = 2

TOPICS = ["dev-tools", "data-analytics", "finance", "communication", "productivity",
          "search-web", "cloud-infra", "ai-ml", "commerce", "media", "security",
          "iot-hardware", "other"]
SENSITIVITY = ["public", "user-private", "financial", "infrastructure-control"]

PROMPT = f"""Label each MCP (Model Context Protocol) server below. Output ONLY a JSON array, no prose, no markdown fences.
Each element: {{"i":<int>,"topic":<topic>,"sensitivity":<sensitivity>}}
topic one of: {",".join(TOPICS)}
sensitivity one of: {",".join(SENSITIVITY)}
sensitivity = the most sensitive thing the server's tools can reach, not the average:
  public = public data only (weather, public docs, open datasets, generic utilities)
  user-private = one user's private data (email, files, calendars, notes, CRM)
  financial = money movement or financial account access (payments, trading, banking)
  infrastructure-control = can change running systems (cloud APIs, deploys, databases)
A server whose tools read public docs AND deploy code is infrastructure-control.
Judge by what the TOOLS can actually DO, never by the subject matter the server talks about.
Documentation, reference, search, quotes and read-only market data about money are `public`
-- only a server whose tools can move money or reach an account balance is `financial`.
Read-only status or info tools about infrastructure are `public` -- only a server whose tools
can change a running system is `infrastructure-control`. If every tool is a get/list/search
/read, the answer is almost always `public` whatever the topic.
If the description is empty, infer from tool names; if still unclear use topic "other", sensitivity "public".
Be decisive. Emit exactly one element per input line, all {{n}} of them, keeping `i` as given.

"""


def render(rows: list[dict]) -> str:
    lines = [
        f"[{i}] {r['name']} | {r['title'] or ''} | {(r['description'] or '')[:150]} | "
        f"tools: {', '.join((r['tool_names'] or [])[:15]) or 'none'}"
        for i, r in enumerate(rows)
    ]
    return PROMPT.replace("{n}", str(len(rows))) + "\n".join(lines)


async def label(rows: list[dict]) -> list[dict]:
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", MODEL, "--output-format", "json", "--allowed-tools", "",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(render(rows).encode())
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {err.decode()[:200]}")
    envelope = json.loads(out)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude error: {str(envelope.get('result'))[:200]}")
    text = re.sub(r"^```(json)?|```$", "", envelope["result"].strip(), flags=re.M).strip()
    parsed = json.loads(text)

    # Validate before writing: a batch that silently drops rows or invents an enum
    # value is worse than one that fails loudly, because the gap is invisible later.
    by_i = {d["i"]: d for d in parsed
            if d.get("topic") in TOPICS and d.get("sensitivity") in SENSITIVITY}
    missing = [i for i in range(len(rows)) if i not in by_i]
    if missing:
        raise RuntimeError(f"{len(missing)} of {len(rows)} rows unlabelled or invalid")

    now = dt.datetime.now(dt.UTC).isoformat()
    return [{
        "snapshot": r["snapshot"], "name": r["name"],
        "topic": by_i[i]["topic"], "sensitivity": by_i[i]["sensitivity"],
        "model": MODEL, "prompt_version": PROMPT_VERSION, "labelled_at": now,
    } for i, r in enumerate(rows)]


async def main(limit: str | None = None) -> None:
    # --relabel re-runs only the high-sensitivity buckets under the current prompt,
    # instead of re-paying for all ~11k servers to fix a directional bias.
    relabel = limit == "--relabel"
    if relabel:
        limit = None

    db = duckdb.connect("registry.duckdb", read_only=True)
    # Derive the snapshot from the data, never from today's date: a run that
    # straddles midnight would otherwise write to a fresh directory, find `done`
    # empty, and re-label every server from scratch.
    day = db.sql("SELECT max(snapshot) FROM servers").fetchone()[0]
    out_path = pathlib.Path("data/snapshots") / day / "labels.jsonl.gz"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    want: set[str] | None = None
    if out_path.exists():
        latest: dict[str, dict] = {}
        with gzip.open(out_path, "rt") as f:
            for ln in f:
                if ln.strip():
                    d = json.loads(ln)
                    latest[d["name"]] = d  # later lines win, as labels.sql does
        done = set(latest)
        if relabel:
            # Suspect bucket AND not yet seen by the current prompt. Excluding
            # servers already on PROMPT_VERSION keeps --relabel resumable, so a
            # run killed by the rate limit resumes instead of starting over.
            want = {n for n, d in latest.items()
                    if d["sensitivity"] in SUSPECT
                    and d.get("prompt_version") != PROMPT_VERSION}
            done = set()  # re-labelling deliberately revisits already-done servers
            print(f"relabelling {len(want)} servers in {SUSPECT} "
                  f"not yet on {PROMPT_VERSION}", file=sys.stderr)

    cols = ["snapshot", "name", "title", "description", "tool_names"]
    rows = [dict(zip(cols, r)) for r in db.sql("""
        SELECT s.snapshot, s.name, s.title, s.description,
               any_value(p.tool_names) AS tool_names
        FROM servers s
        JOIN remotes r       ON r.snapshot = s.snapshot AND r.server_name = s.name
        JOIN endpoint_auth a ON a.snapshot = r.snapshot AND a.url = r.url
        LEFT JOIN probes p   ON p.snapshot = r.snapshot AND p.url = r.url
        -- Every remote server, not just reachable ones. Restricting to depth >= 2
        -- makes the analysis circular: a server that forces auth never shows its
        -- tool list, so "who gates public data" would be unanswerable by
        -- construction. Gated servers get labelled from name + description alone;
        -- labels.sql flags which labels had tool names behind them.
        GROUP BY ALL ORDER BY s.name
    """).fetchall()]
    db.close()

    rows = [r for r in rows if r["name"] not in done]
    if want is not None:
        rows = [r for r in rows if r["name"] in want]
    if limit:
        rows = rows[: int(limit)]
    batches = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    print(f"{len(rows)} servers in {len(batches)} batches ({len(done)} done)", file=sys.stderr)

    sem = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    ok = failed = 0
    with gzip.open(out_path, "at") as f:
        async def one(batch: list[dict]) -> None:
            nonlocal ok, failed
            async with sem:
                for attempt in range(RETRIES + 1):
                    try:
                        labels = await label(batch)
                        break
                    except Exception as e:
                        if attempt == RETRIES:
                            failed += 1
                            print(f"\nbatch failed ({type(e).__name__}: {e}) -- "
                                  f"re-run to retry its {len(batch)} servers",
                                  file=sys.stderr)
                            return
                        await asyncio.sleep(5 * 2 ** attempt)
            async with lock:
                for row in labels:
                    f.write(json.dumps(row, separators=(",", ":")) + "\n")
                f.flush()
                ok += 1
                print(f"\r{ok + failed}/{len(batches)} batches ({failed} failed)",
                      end="", file=sys.stderr, flush=True)

        await asyncio.gather(*(one(b) for b in batches))
    print(f"\n{out_path} -- {ok} batches ok, {failed} failed", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
