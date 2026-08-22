DUCKDB = uv run --with duckdb python -c "import duckdb,sys;duckdb.connect('registry.duckdb').execute(sys.stdin.read())"

fetch:  ; ./fetch.py
build:  ; $(DUCKDB) < build.sql
	@ls data/snapshots/*/probes.jsonl.gz >/dev/null 2>&1 && $(DUCKDB) < probes.sql || echo "no probes yet, skipping probes.sql"
	@ls data/snapshots/*/authservers.jsonl.gz >/dev/null 2>&1 && $(DUCKDB) < authservers.sql || echo "no authservers yet, skipping authservers.sql"
	@ls data/snapshots/*/labels.jsonl.gz >/dev/null 2>&1 && $(DUCKDB) < labels.sql || echo "no labels yet, skipping labels.sql"

authservers: ; ./authservers.py
classify:    ; ./classify.py
probe:  ; ./probe.py
sql:    ; uv run --with duckdb python -i -c "import duckdb;db=duckdb.connect('registry.duckdb')"

.PHONY: fetch build probe authservers classify sql export figures

export: ; @mkdir -p data/site && $(DUCKDB) < export.sql && ls -la data/site/

figures: build export
	./render.py
