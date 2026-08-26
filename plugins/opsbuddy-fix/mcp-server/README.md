# Databricks Lineage MCP Server

A small MCP server that gives Claude (or any MCP client) tools for tracing a Databricks job or
run: job/run telemetry, the task dependency graph, source file/notebook content, job-to-job
orchestration, and Unity Catalog table lineage. It was built to match the tool contract the
`databricks-job-lineage` skill expects — connect this server and that skill works with no
changes.

It authenticates with a single Databricks personal access token (PAT). Six of its seven tools
are pure reads; the seventh, `trigger_job_run`, re-runs a real job to prove a fix actually works
(opsbuddy-fix-style real-verification) and is gated behind an allowlist / explicit human
approval — see its row below and its docstring in `server.py`.

## 1. Install

This directory is packaged for [`uv`](https://docs.astral.sh/uv/) (`pyproject.toml` +
`uv.lock`) — that's what the plugin's `.mcp.json` invokes, and it needs no manual venv step:

```bash
cd mcp-server
uv run main.py   # first run resolves deps into an isolated .venv and generates uv.lock
```

If you don't have `uv`, plain pip still works:

```bash
cd mcp-server
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python server.py
```

**Note:** this is a duplicated copy of the same server bundled with the `databricks-job-lineage`
plugin — kept independent on purpose so `opsbuddy-fix` is installable on its own, without
requiring the other plugin. Keep the two in sync if you change `server.py`.

## 2. Configure the PAT token and host

Generate a token in your Databricks workspace: **Settings → Developer → Access tokens →
Generate new token**. It only needs read access to jobs, workspace, and (optionally) SQL
warehouses — this server has no code path that writes or deletes anything.

```bash
cp .env.example .env
```

Edit `.env` (or just `export` these directly in your shell):

```bash
export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
export DATABRICKS_TOKEN=dapi...your-token...

# Optional -- only needed for get_table_lineage
export DATABRICKS_SQL_WAREHOUSE_ID=

# Optional -- caps how many jobs get_job_orchestration scans when looking for upstream triggers
export DATABRICKS_ORCHESTRATION_SCAN_LIMIT=200
```

## 3. Run it standalone to sanity-check auth

```bash
python server.py
```

On success you'll see `Connected to https://... as you@company.com` on stderr, and the process
will sit there waiting on stdio — that's normal for an MCP server, it's not meant to print
anything else until a client talks to it. Ctrl-C to stop. If the host/token are wrong you'll get
a clear `FATAL:` message instead of a silent hang.

## 4. Register it with your MCP client

**As part of this plugin** (the normal path): the plugin's own `../.mcp.json` already points at
this server via `${CLAUDE_PLUGIN_ROOT}` — a variable Claude Code resolves to wherever the plugin
actually got installed (it gets copied into `~/.claude/plugins/cache/...`, so a hardcoded or even
relative path here would break). Nothing to configure by hand; just set the env vars from step 2
before installing the plugin.

**Standalone, outside the plugin** — add to your MCP config (`claude_desktop_config.json` or
equivalent) directly:

```json
{
  "mcpServers": {
    "databricks-lineage": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/mcp-server", "main.py"],
      "env": {
        "DATABRICKS_HOST": "https://your-workspace.cloud.databricks.com",
        "DATABRICKS_TOKEN": "dapi...your-token...",
        "DATABRICKS_SQL_WAREHOUSE_ID": ""
      }
    }
  }
}
```

Restart the client, then confirm all eight tools show up (`get_latest_failed_run`, `get_job_run`,
`get_job_config`, `get_source_file`, `get_job_orchestration`, `get_table_lineage`, `sync_repo`,
`trigger_job_run`).

**Cowork / claude.ai (remote sessions)** — this server runs over stdio, which only works for a
client that can launch a local process (Desktop, Code). Using it from a cloud session like this
one requires deploying it somewhere network-reachable and wrapping it with an HTTP/SSE transport
and its own auth in front of the PAT — a bigger step than what's here. Say so if that's what you
need next and it can be built as a follow-up.

## What each tool does

| Tool | Databricks API behind it | Notes |
|---|---|---|
| `get_latest_failed_run` | Jobs API `runs/list` | Walks recent runs for the first `FAILED` one. |
| `get_job_run` | Jobs API `runs/get` + `runs/get-output` per failed task | Full stack trace, not truncated. |
| `get_job_config` | Jobs API `jobs/get` | Task `depends_on` graph + schedule. |
| `get_source_file` | Workspace API `workspace/export` | Notebook or file source, base64-decoded. |
| `get_job_orchestration` | Jobs API `jobs/get` + bounded `jobs/list` scan | Downstream is cheap (this job's own Run Job tasks); upstream is a capped scan since the Jobs API has no reverse index. |
| `get_table_lineage` | Unity Catalog system table `system.access.table_lineage` via a SQL warehouse | Needs UC lineage tracking enabled + `DATABRICKS_SQL_WAREHOUSE_ID` set. |
| `sync_repo` | Repos API `repos/update` | **Writes.** Points a Databricks Repo checkout at a branch — needed before `trigger_job_run` since Repos doesn't auto-sync on push. |
| `trigger_job_run` | Jobs API `jobs/run-now` + polled `runs/get` | **Writes.** Re-runs a job to prove a fix works. Refused unless the job is in `DATABRICKS_TRIGGER_ALLOWLIST` or the caller passes `force="true"` after explicit human approval. |

## The one part that may need tuning: `get_table_lineage`

Everything else here rests on well-documented, stable Jobs/Workspace REST APIs. `get_table_lineage`
is different — it queries `system.access.table_lineage`, a Unity Catalog system table whose exact
column names have changed across Databricks releases and can differ by workspace. The query in
`server.py` assumes columns named `entity_type`, `entity_run_id`, `source_table_full_name`, and
`target_table_full_name`. If it errors out, run this in a SQL editor against your workspace:

```sql
DESCRIBE system.access.table_lineage;
```

and adjust the column names in `get_table_lineage()` in `server.py` to match. Until it's tuned
(or if UC lineage isn't enabled at all), the tool fails soft — it returns an `error` field instead
of crashing, and the skill reports that as "couldn't check" rather than "no lineage exists."

## `trigger_job_run`: the one tool that writes

Re-running a real job can execute code that writes real data — possibly from an unmerged,
not-yet-human-reviewed branch. So `trigger_job_run` is refused by default:

- Set `DATABRICKS_TRIGGER_ALLOWLIST` to a comma-separated list of job IDs that are safe to
  auto-re-run (sandbox/staging jobs), or to `"all"` — only ever in a non-production workspace.
- Otherwise, the caller must pass `force="true"`, which should only happen after a human has
  explicitly approved re-running that specific job. Claude should ask first, not decide this on
  its own — the tool's docstring says so explicitly.

A refused call returns `{"error": ..., "needs_approval": true}` rather than raising, so a
client can surface that as "waiting on your approval" instead of a failure.

## Error handling philosophy

Every tool returns an `error` key instead of raising when something goes wrong (bad job ID,
permission denied, a system table that doesn't exist), and returns genuinely empty lists/nulls
when there's honestly nothing there (no downstream jobs, no tables touched). The
`databricks-job-lineage` skill relies on that distinction — it reports the two very differently
in the final lineage report — so keep it if you extend this server. `trigger_job_run` follows
the same convention even though it's not a pure read.
