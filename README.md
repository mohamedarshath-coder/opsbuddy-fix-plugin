# opsbuddy-fix marketplace

A Claude Code plugin marketplace (`.claude-plugin/marketplace.json`) for Databricks incident
tooling. Add it as a marketplace, then install whichever plugin(s) you need:

| Plugin | What it does | Depends on |
|---|---|---|
| [`databricks-job-lineage`](plugins/databricks-job-lineage/) | Read-only tracing for a Databricks job/run: telemetry, dependency graph, source, orchestration, Unity Catalog table lineage. | `databricks-lineage` MCP server (bundled) |
| [`opsbuddy-fix`](plugins/opsbuddy-fix/) | Autonomous diagnose → fix → PR → review → real re-run verification pipeline for a failed Databricks job. | `databricks-lineage` MCP server (bundled) + `github` MCP server (`npx`) |

Each plugin bundles its own copy of the `databricks-lineage` MCP server source under
`mcp-server/` — duplicated deliberately so either plugin installs and works standalone, with no
cross-plugin runtime dependency. See each plugin's own `README.md` for its required environment
variables and install notes before adding it.

## Why this shape

- **Marketplace wrapper** (`.claude-plugin/marketplace.json` + `plugins/<name>/`): this repo can
  hold more than one plugin, browsable and installable independently, instead of being a single
  standalone plugin folder.
- **Bundled MCP server source, not an absolute path**: when a plugin installs from a marketplace,
  Claude Code copies it into `~/.claude/plugins/cache/...` — a hardcoded or even relative path in
  `.mcp.json` would break there. Each `.mcp.json` instead uses `${CLAUDE_PLUGIN_ROOT}`, which
  Claude Code resolves to the plugin's real install location.
- **`uv` + `pyproject.toml`** instead of a hand-built `.venv` + `requirements.txt`: lets the
  server bootstrap its own isolated environment on first run (`uv run`), so installing the plugin
  doesn't require a pre-existing venv with dependencies already `pip install`-ed into it. Plain
  pip still works as a documented fallback in each `mcp-server/README.md`.

## Note on `uv.lock`

Neither bundled `mcp-server/` currently ships a committed `uv.lock` — this machine doesn't have
`uv` installed, so one couldn't be generated here without fabricating version/hash data. Running
`uv run main.py` once (with `uv` installed) will generate it; commit the resulting `uv.lock` in
both `plugins/databricks-job-lineage/mcp-server/` and `plugins/opsbuddy-fix/mcp-server/` after
that.
