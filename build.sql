-- Rebuild registry.duckdb from the raw snapshots. Idempotent, disposable output.
--   duckdb registry.duckdb < build.sql
-- New question? Add a column here and re-run. Never hand-edit the database.

CREATE OR REPLACE TABLE raw AS
SELECT regexp_extract(filename, 'snapshots/([0-9-]+)/', 1) AS snapshot, server, _meta
FROM read_json('data/snapshots/*/servers.jsonl.gz',
                format = 'newline_delimited', filename = true, sample_size = -1)
WHERE regexp_extract(filename, 'snapshots/([0-9-]+)/', 1) = getvariable('snapshot');

CREATE OR REPLACE TABLE servers AS
SELECT
    snapshot,
    server.name                                   AS name,
    server.version                                AS version,
    server.title                                  AS title,
    server.description                            AS description,
    server.websiteUrl                             AS website_url,
    server.repository.url                         AS repo_url,
    server.repository.source                      AS repo_source,
    len(coalesce(server.remotes, []))             AS n_remotes,
    len(coalesce(server.packages, []))            AS n_packages,
    CASE
        WHEN n_remotes > 0 AND n_packages > 0 THEN 'hybrid'
        WHEN n_remotes > 0                    THEN 'remote'
        WHEN n_packages > 0                   THEN 'local'
        ELSE 'undeployable'
    END                                           AS shape,
    _meta['io.modelcontextprotocol.registry/official'].status       AS status,
    _meta['io.modelcontextprotocol.registry/official'].publishedAt::TIMESTAMP AS published_at,
    _meta['io.modelcontextprotocol.registry/official'].updatedAt::TIMESTAMP   AS updated_at
FROM raw;

-- One row per (server, declared remote endpoint).
CREATE OR REPLACE TABLE remotes AS
SELECT
    r.snapshot,
    r.server.name                                        AS server_name,
    t.type                                               AS transport,
    t.url                                                AS url,
    lower(regexp_extract(t.url, '^https?://([^/:?#]+)', 1)) AS host,
    -- Naive eTLD+1 (breaks on suffixes such as .co.uk). Host-level results use
    -- the full host field and do not depend on this approximation.
    array_to_string(host.split('.')[greatest(1, len(host.split('.')) - 1):], '.') AS domain,
    contains(t.url, '{')                                 AS url_templated,
    len(coalesce(t.headers, []))                         AS n_headers,
    list_transform(coalesce(t.headers, []), h -> h.name) AS header_names,
    list_bool_or(list_transform(coalesce(t.headers, []),
        h -> lower(h.name) IN ('authorization', 'x-api-key', 'api-key')
             OR coalesce(h.isSecret, false)))            AS declares_auth_header
FROM raw r, UNNEST(r.server.remotes) AS u(t)
WHERE r.server.remotes IS NOT NULL;

CREATE OR REPLACE TABLE packages AS
SELECT
    r.snapshot,
    r.server.name       AS server_name,
    p.registryType      AS registry_type,
    p.identifier        AS identifier,
    p.version           AS version,
    p.runtimeHint       AS runtime_hint,
    p.transport.type    AS transport
FROM raw r, UNNEST(r.server.packages) AS u(p)
WHERE r.server.packages IS NOT NULL;

-- Probe targets: one row per distinct URL, however many servers point at it.
CREATE OR REPLACE TABLE endpoints AS
SELECT snapshot, url, any_value(host) AS host, any_value(domain) AS domain,
       count(*) AS n_servers, bool_or(url_templated) AS url_templated
FROM remotes GROUP BY snapshot, url;
