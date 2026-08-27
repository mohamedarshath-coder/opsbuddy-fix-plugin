# opsbuddy-fix-full plugin

Four chained skills for a failed Databricks job, plus the `root-cause-analysis` subagent they
depend on. Fully portable — installable in any project.

| Skill | Does |
|---|---|
| `opsbuddy-fix` | Orchestrator — 11 phases: diagnose, Jira ticket, feasibility gate, resolve repo, fix, PR, review, real re-run verification gate, Slack alert, incident log, summary |
| `databricks-debug` | Phase 2 — telemetry -> 11-category classification -> two independent `root-cause-analysis` agent runs, reconciled fail-closed on disagreement |
| `testing` | Phase 5 — static validation: real tool execution when a local checkout exists, a careful reasoning pass otherwise |
| `pr-review-opsbuddy-fix` | Phase 8 — 7-point Mode A checklist validating the diff against the confirmed root cause |

## Why this one is portable (and the earlier `opsbuddy-fix-scripts` attempt wasn't)

That version shelled out to `workflow/*.py` scripts living in one specific repo, and cloned the
target repo locally to apply a fix — neither of which travels with an installed plugin. This
version:

- **Bundles its own Databricks MCP server** (`mcp-server/`, with a real `.venv` committed
  in-place — no `uv run`/PATH bootstrap needed at install time) instead of calling
  `workflow/databricks_workflow.py`.
- **Applies fixes via the GitHub MCP server's content APIs** (fetch file -> edit text -> push) —
  the same fetch → edit → push shape any GitHub MCP server, official or built-in connector,
  supports — instead of a local `git clone`. See `skills/opsbuddy-fix/references/
  github_mcp_interface.md` for the tool contract and how to connect one if you don't have one.
- **Talks to Jira through any connected Jira/Atlassian MCP** (see `skills/opsbuddy-fix/
  references/jira_mcp_interface.md`) instead of a repo-local script.
- **Slack alerting is a small, dependency-free script** (`scripts/slack_alert.py` — just
  `requests` + one env var, no imports from any host project).
- **The `root-cause-analysis` agent reasons over source text handed to it** (fetched via the
  bundled server's `get_source_file`) instead of assuming a local checkout it can Read/Grep/Glob.

The one place portability is inherently limited: `testing`'s "real execution" mode still needs an
actual local checkout and the target repo's own toolchain to be useful — that's true of any
static-validation step, not a gap specific to this plugin. When neither is available (the normal
case here, since fixes are applied via GitHub's content API with no local clone), it falls back
to a reasoning pass and says so explicitly rather than pretending it ran real tools.

## Required setup

- **Databricks** (bundled server): `DATABRICKS_HOST`, `DATABRICKS_TOKEN`. Optional:
  `DATABRICKS_SQL_WAREHOUSE_ID` (needed for `get_table_lineage` and `log_incident`),
  `DATABRICKS_OPS_INCIDENT_TABLE` (three-part table name for `log_incident`),
  `DATABRICKS_TRIGGER_ALLOWLIST` (job IDs safe to auto-re-run at Gate 8.5).
- **GitHub**: any connected MCP server — see `skills/opsbuddy-fix/references/
  github_mcp_interface.md` for the three supported options and how to connect each.
- **Jira**: any connected MCP server (Claude's built-in Atlassian connector is the easy default)
  — see `skills/opsbuddy-fix/references/jira_mcp_interface.md`. Optional — the run continues
  without it, just without ticket tracking.
- **Slack**: `SLACK_WEBHOOK_URL` — an incoming webhook for wherever incident alerts should post.

## Differences from the original spec this was built from

- **GitHub**, not Azure DevOps
- **Slack**, not an Email MCP
- **Any Jira/Atlassian MCP**, not a fixed `corp-jira` server
