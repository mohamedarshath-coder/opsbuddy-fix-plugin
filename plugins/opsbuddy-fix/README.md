# opsbuddy-fix plugin

Bundles the `opsbuddy-fix` skill (diagnose → fix → PR → review → real re-run verification) with
the two MCP servers it depends on:

- **`opsbuddy-databricks-lineage`** — bundled inside this plugin at `mcp-server/` (8 tools, including the
  write-capable `sync_repo`/`trigger_job_run`). This is an independent copy of the same server
  the `databricks-job-lineage` plugin bundles — duplicated on purpose so this plugin installs and
  runs standalone, with no dependency on the other plugin being present.
- **`github`** — the community `server-github` package via `npx`, no bundling needed.

Both are wired up in `.mcp.json` using `${CLAUDE_PLUGIN_ROOT}`, which Claude Code resolves to
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
| `GITHUB_PERSONAL_ACCESS_TOKEN` | Yes | Fine-grained PAT — needs Contents (Read/write) and Pull requests (Read/write) on whichever repos you'll target |
| `NODE_EXTRA_CA_CERTS` | Only if your network intercepts TLS (e.g. Zscaler) | Path to the exported corporate root CA `.crt` file — see below |

**About `NODE_EXTRA_CA_CERTS`**: if you're on a corporate network that inspects HTTPS traffic
(Zscaler, Netskope, Forcepoint, etc.), the `github` server's underlying Node.js process will fail
every call with `UNABLE_TO_GET_ISSUER_CERT_LOCALLY` unless this is set to a certificate file Node
trusts. Export your network's root CA from Windows' trusted store (`Cert:\LocalMachine\Root` in
PowerShell) to a `.crt` file and point this variable at it. Skip this entirely on a network
without TLS inspection.

### 2. Have `uv` available (or fall back to pip)

`mcp-server/.mcp.json` invokes `uv run --directory ... main.py`, which resolves dependencies into
an isolated environment on first launch — no manual venv setup required. If `uv` isn't available
on the install machine, see `mcp-server/README.md` for the plain-pip fallback and adjust
`.mcp.json`'s `command`/`args` accordingly.

### 3. Confirm `${VAR}` env-var substitution actually works in your install path

This is separate from the `${CLAUDE_PLUGIN_ROOT}` path resolution above (that one's guaranteed by
Claude Code itself) — this is about whether the `${VAR}` placeholders inside `.mcp.json`'s `env`
block get substituted from your actual shell/session environment variables in your specific
install context (Desktop vs. Code, project vs. user-level install). After installing, check
whether the `opsbuddy-databricks-lineage` and `github` servers actually start (Desktop's MCP servers panel
should show them as "running"). If they fail to start or the tools don't show up, the most likely
cause is that substitution didn't happen — in that case, replace the `${VAR}` placeholders in
`.mcp.json` with the real values directly (do this yourself, in your own editor — not by pasting
them into a chat).

## After installing

Verify all 15 tools are available (8 from `opsbuddy-databricks-lineage`, 7 from `github`), then try:
- *"Trace the lineage for job &lt;id&gt;"* → should invoke `databricks-job-lineage`-style tools
- *"Fix job &lt;id&gt;"* / *"opsbuddy-fix run &lt;id&gt;"* → should invoke `opsbuddy-fix`

No Jira MCP is bundled — `opsbuddy-fix`'s ticket-related steps produce ready-to-paste text
instead of posting anywhere, and say so explicitly in its output.
