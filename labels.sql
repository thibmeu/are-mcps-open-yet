-- Topic and sensitivity labels -> the headline cross-tab.

CREATE OR REPLACE TABLE labels AS
SELECT * FROM read_json('data/snapshots/*/labels.jsonl.gz',
                         format = 'newline_delimited', union_by_name = true, sample_size = -1)
WHERE snapshot = getvariable('snapshot')
QUALIFY row_number() OVER (PARTITION BY snapshot, name ORDER BY labelled_at DESC) = 1;

-- Compare inferred capability sensitivity with observed access posture. These
-- labels do not explain why an operator requires or advertises authentication.
--
-- `had_tools` is the confidence caveat and must be reported alongside any claim
-- about gated servers: a server that forces auth never shows its tool list, so its
-- label rests on name and description alone.
CREATE OR REPLACE VIEW gate_vs_sensitivity AS
SELECT l.sensitivity, a.posture, a.depth,
       coalesce(p.n_tools, 0) > 0 AS had_tools,
       count(DISTINCT s.name) AS servers,
       count(DISTINCT a.host) AS hosts
FROM labels l
JOIN servers s       ON s.snapshot = l.snapshot AND s.name = l.name
JOIN remotes r       ON r.snapshot = s.snapshot AND r.server_name = s.name
JOIN endpoint_auth a ON a.snapshot = r.snapshot AND a.url = r.url
LEFT JOIN probes p    ON p.snapshot = r.snapshot AND p.url = r.url
GROUP BY ALL;
