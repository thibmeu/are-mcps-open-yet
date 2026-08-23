-- Probe results -> auth posture. Run after build.sql (make build does both).

CREATE OR REPLACE TABLE probes AS
SELECT regexp_extract(filename, 'snapshots/([0-9-]+)/', 1) AS snapshot, * EXCLUDE (filename)
FROM read_json('data/snapshots/*/probes.jsonl.gz',
                format = 'newline_delimited', filename = true,
                union_by_name = true, sample_size = -1)
WHERE regexp_extract(filename, 'snapshots/([0-9-]+)/', 1) = getvariable('snapshot')
-- --retry appends a second row per URL; last attempt wins.
QUALIFY row_number() OVER (PARTITION BY snapshot, url ORDER BY probed_at DESC) = 1;

-- One row per probed endpoint, with the posture derived from gate depth plus
-- whether the server advertises OAuth at all.
CREATE OR REPLACE VIEW endpoint_auth AS
SELECT
    p.snapshot,
    p.url,
    e.host,
    e.domain,
    e.n_servers,
    p.depth,
    p.init_status,
    p.tools_status,
    p.n_tools,
    p.www_authenticate IS NOT NULL       AS challenged,
    -- Spec SHOULD: include `scope` in the challenge so a client knows what to
    -- ask for. Free to measure, since the raw header was captured.
    p.www_authenticate LIKE '%scope=%'   AS scope_in_challenge,
    p.prm_status = 200                   AS advertises_oauth,
    coalesce(len(p.auth_servers), 0) > 0 AS has_auth_server,
    CASE
        -- 402 is a paywall, not a broken endpoint, and not an auth gate either.
        WHEN p.init_status = 402 THEN 'paywalled'
        -- We throttled ourselves out of these; posture is unknown, not absent.
        WHEN p.init_status = 429 THEN 'throttled'
        -- A 404 or a dead host is not a security posture. Keep it separate or
        -- every "% requiring auth" number is inflated by dead registrations.
        WHEN p.depth = 0 THEN 'broken'
        -- Forces auth: no anonymous path to the tool list, with a real challenge.
        WHEN p.depth = 1 THEN 'required'
        WHEN p.depth = 2 AND p.tools_status IN (401, 403) THEN 'required'
        -- initialize worked but tools/list failed for a non-auth reason.
        WHEN p.depth = 2 THEN 'partial'
        -- Requests auth: tool schemas served anonymously, OAuth advertised.
        WHEN p.depth = 3 AND (p.prm_status = 200 OR p.www_authenticate IS NOT NULL)
            THEN 'optional'
        ELSE 'open'
    END AS posture
FROM probes p
LEFT JOIN endpoints e USING (snapshot, url);

-- Server-level join. A server with several remotes takes its weakest gate,
-- since an agent only needs one way in.
CREATE OR REPLACE VIEW server_auth AS
SELECT
    s.snapshot, s.name, s.title, s.description, s.shape, s.repo_url,
    max(a.depth)                        AS max_depth,
    bool_or(a.advertises_oauth)         AS advertises_oauth,
    bool_or(r.declares_auth_header)     AS byo_token,
    list(DISTINCT a.posture)            AS postures,
    any_value(a.host)                   AS host
FROM servers s
JOIN remotes r      ON r.snapshot = s.snapshot AND r.server_name = s.name
LEFT JOIN endpoint_auth a ON a.snapshot = r.snapshot AND a.url = r.url
GROUP BY ALL;
