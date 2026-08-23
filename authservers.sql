-- Authorization-server metadata -> how a client could obtain a client_id.

CREATE OR REPLACE TABLE authservers AS
SELECT regexp_extract(filename, 'snapshots/([0-9-]+)/', 1) AS snapshot, * EXCLUDE (filename)
FROM read_json('data/snapshots/*/authservers.jsonl.gz',
                format = 'newline_delimited', filename = true,
                union_by_name = true, sample_size = -1)
WHERE regexp_extract(filename, 'snapshots/([0-9-]+)/', 1) = getvariable('snapshot')
QUALIFY row_number() OVER (PARTITION BY snapshot, issuer ORDER BY fetched_at DESC) = 1;

-- Each endpoint inherits the best client_id path any of its authorization
-- servers offers -- a client only needs one way in.
CREATE OR REPLACE VIEW endpoint_client_id AS
-- Explode first, join second: DuckDB rejects a LEFT JOIN against a correlated UNNEST.
WITH exploded AS (
    SELECT p.snapshot, p.url, trim(u.iss::VARCHAR, '"') AS issuer
    FROM probes p, UNNEST(p.auth_servers) AS u(iss)
    WHERE p.auth_servers IS NOT NULL
), x AS (
    SELECT e.snapshot, e.url, a.*
    FROM exploded e
    LEFT JOIN authservers a ON a.snapshot = e.snapshot AND a.issuer = e.issuer
)
SELECT snapshot, url,
       bool_or(cimd_supported)  AS cimd,
       bool_or(advertises_dcr)  AS dcr,
       count(*)                 AS n_auth_servers,
       count(metadata_url)      AS n_with_metadata,
       CASE WHEN bool_or(cimd_supported) THEN 'cimd'
            WHEN bool_or(advertises_dcr) THEN 'dcr'
            WHEN count(metadata_url) = 0 THEN 'no-metadata'
            ELSE 'preregistered' END AS client_id_path
FROM x GROUP BY 1, 2;
