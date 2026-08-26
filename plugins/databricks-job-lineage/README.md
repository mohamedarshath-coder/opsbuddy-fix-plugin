# databricks-job-lineage plugin

Bundles the `databricks-job-lineage` skill (read-only diagnosis/tracing for a Databricks job or
run) with the MCP server it depends on, `databricks-lineage`, bundled inside this plugin at
`mcp-server/`.

This is the same server bundled with the `opsbuddy-fix` plugin — duplicated there on purpose so
each plugin installs and runs standalone. Install this one alone if you only want read-only
lineage tracing, with no PR/git write capability.

`.mcp.json` wires the server up via `${CLAUDE_PLUGIN_ROOT}`, which Claude Code resolves to
wherever this plugin actually got installed (its cache directory, not this source checkout) — so
the server path stays correct regardless of who installs this or from where.

## Before installing

### 1. Set the required environment variables

`.mcp.json` references these by name (`${VAR}`) rather than containing real values — no
credentials are stored in this plugin folder:

| Variable | Required | Notes |
|---|---|---|
| `DATABRICKS_HOST` | Yes | Your workspace URL |
| `DATABRICKS_TOKEN` | Yes | PAT with permission to run jobs you allowlist below |
| `DATABRICKS_SQL_WAREHOUSE_ID` | No | Only needed for `get_table_lineage` |
| `DATABRICKS_TRIGGER_ALLOWLIST` | No | Comma-separated job IDs safe to auto-re-run without asking a human first. Leave unset to require approval every time (safe default). |

### 2. Have `uv` available (or fall back to pip)

`.mcp.json` invokes `uv run --directory ... main.py`, which resolves dependencies into an
isolated environment on first launch — no manual venv setup required. If `uv` isn't available on
the install machine, see `mcp-server/README.md` for the plain-pip fallback and adjust
`.mcp.json`'s `command`/`args` accordingly.

### 3. Confirm `${VAR}` env-var substitution actually works in your install path

Separate from `${CLAUDE_PLUGIN_ROOT}` path resolution (guaranteed by Claude Code itself) — this
is about whether the `${VAR}` placeholders in `.mcp.json`'s `env` block get substituted from your
actual shell/session environment in your install context. After installing, check whether
`databricks-lineage` actually starts. If it doesn't, replace the placeholders with real values
directly in your own editor — not by pasting them into a chat.

## After installing

Verify all 8 tools are available, then try *"Trace the lineage for job &lt;id&gt;"* or *"What
tables does run &lt;id&gt; write to?"*.
