-- Aggregates for the blog page, one small JSON file per panel.
--   make export
-- These are committed artifacts: the Hugo site never runs DuckDB or Python.
-- Safe to re-run after --relabel; the panels regenerate from whatever is current.

-- 1. Shape of the registry. Sets the denominator.
COPY (
    SELECT shape, count(*) AS servers
    FROM servers GROUP BY 1 ORDER BY servers DESC
) TO 'data/site/shape.json' (FORMAT JSON, ARRAY true);

-- 2. The gate-depth ladder. Ordinal: how far an anonymous client gets.
COPY (
    SELECT depth,
           CASE depth WHEN 0 THEN 'unreachable'
                      WHEN 1 THEN 'gated at transport'
                      WHEN 2 THEN 'gated at tools/list'
                      ELSE 'open through tools/list' END AS rung,
           count(*)               AS endpoints,
           count(DISTINCT host)   AS hosts
    FROM endpoint_auth GROUP BY 1, 2 ORDER BY depth
) TO 'data/site/ladder.json' (FORMAT JSON, ARRAY true);

-- 3. Posture, counted BOTH ways. The disagreement is the point, so the panel
--    needs both series on one shared axis.
COPY (
    SELECT posture,
           count(*)             AS endpoints,
           count(DISTINCT host) AS hosts
    FROM endpoint_auth GROUP BY 1 ORDER BY endpoints DESC
) TO 'data/site/posture.json' (FORMAT JSON, ARRAY true);

-- 4. Concentration. One host owns 12% of the remote registry.
COPY (
    SELECT host, count(*) AS endpoints,
           round(100.0 * count(*) / (SELECT count(*) FROM endpoint_auth), 1) AS pct
    FROM endpoint_auth GROUP BY 1 ORDER BY endpoints DESC, host LIMIT 12
) TO 'data/site/concentration.json' (FORMAT JSON, ARRAY true);

-- 5. Sensitivity x posture. This exploratory cross-tab compares the inferred
--    capability class with the access behavior observed by the probe.
COPY (
    SELECT l.sensitivity, a.posture,
           count(DISTINCT s.name) AS servers,
           count(DISTINCT a.host) AS hosts,
           -- Gated servers never show a tool list, so their label rests on name
           -- and description alone. The post must surface this, not bury it.
           count(DISTINCT s.name) FILTER (WHERE coalesce(p.n_tools, 0) > 0) AS servers_with_tools
    FROM labels l
    JOIN servers s        ON s.snapshot = l.snapshot AND s.name = l.name
    JOIN remotes r        ON r.snapshot = s.snapshot AND r.server_name = s.name
    JOIN endpoint_auth a  ON a.snapshot = r.snapshot AND a.url = r.url
    LEFT JOIN probes p    ON p.snapshot = r.snapshot AND p.url = r.url
    GROUP BY 1, 2 ORDER BY servers DESC, l.sensitivity, a.posture
) TO 'data/site/sensitivity.json' (FORMAT JSON, ARRAY true);

-- 6. The consent ledger. Gated endpoints against distinct authorization servers:
--    near 1:1 means an agent amortises nothing across the ecosystem.
COPY (
    WITH exploded AS (
        SELECT c.url, trim(u.iss::VARCHAR, '"') AS issuer
        FROM endpoint_auth c
        JOIN probes p ON p.snapshot = c.snapshot AND p.url = c.url,
             UNNEST(p.auth_servers) AS u(iss)
        WHERE c.posture = 'required'
    )
    SELECT count(DISTINCT url) AS gated_endpoints,
           count(DISTINCT issuer) AS distinct_issuers,
           round(count(DISTINCT url)::DOUBLE / nullif(count(DISTINCT issuer), 0), 2)
               AS endpoints_per_issuer
    FROM exploded
) TO 'data/site/consent.json' (FORMAT JSON, ARRAY true);

-- 7. How an agent could obtain a client_id at all. CIMD is the spec's SHOULD;
--    DCR is deprecated as of 2026-07-28, 17 days before this snapshot.
COPY (
    SELECT client_id_path, count(*) AS auth_servers,
           round(100.0 * count(*) / (SELECT count(*) FROM authservers), 1) AS pct
    FROM authservers GROUP BY 1 ORDER BY auth_servers DESC
) TO 'data/site/client_id_path.json' (FORMAT JSON, ARRAY true);

-- 8. Protocol version lag. Ten servers speak the current spec.
COPY (
    SELECT negotiated_protocol::VARCHAR AS protocol, count(*) AS servers
    FROM probes WHERE negotiated_protocol IS NOT NULL
    GROUP BY 1 ORDER BY servers DESC
) TO 'data/site/protocol.json' (FORMAT JSON, ARRAY true);
