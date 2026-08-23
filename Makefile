SNAPSHOT ?= $(notdir $(shell find data/snapshots -mindepth 1 -maxdepth 1 -type d | sort | tail -1))
DUCKDB = uv run --with duckdb python -c "import duckdb,sys;db=duckdb.connect('registry.duckdb');db.execute('SET VARIABLE snapshot = ?', [sys.argv[1]]);db.execute(sys.stdin.read())" $(SNAPSHOT)

fetch:  ; ./fetch.py
build:  ; $(DUCKDB) < build.sql
	@ls data/snapshots/*/probes.jsonl.gz >/dev/null 2>&1 && $(DUCKDB) < probes.sql || echo "no probes yet, skipping probes.sql"
	@ls data/snapshots/*/authservers.jsonl.gz >/dev/null 2>&1 && $(DUCKDB) < authservers.sql || echo "no authservers yet, skipping authservers.sql"
	@ls data/snapshots/*/labels.jsonl.gz >/dev/null 2>&1 && $(DUCKDB) < labels.sql || echo "no labels yet, skipping labels.sql"

authservers: ; ./authservers.py
classify:    ; ./classify.py
probe:  ; ./probe.py
sql:    ; uv run --with duckdb python -i -c "import duckdb;db=duckdb.connect('registry.duckdb')"

.PHONY: fetch build probe authservers classify sql result export figures

result: build
	@uv run --with duckdb python -c "import duckdb;db=duckdb.connect('registry.duckdb');n,total=db.execute(\"SELECT count(*) FILTER (WHERE posture='open'), count(*) FROM endpoint_auth\").fetchone();print(f'{n} of {total} remote endpoints ({100*n/total:.1f}%) expose tools/list anonymously')"

export: ; @mkdir -p data/site && $(DUCKDB) < export.sql && ls -la data/site/

figures: build export
	./render.py
