# opsbuddy-fix-scripts plugin

Four chained skills for a failed Databricks job in the **ai-sdlc-devops** repo, plus the
`root-cause-analysis` subagent they depend on:

| Skill | Does |
|---|---|
| `opsbuddy-fix` | Orchestrator — 11 phases: diagnose, Jira ticket, feasibility gate, branch, fix, PR, review, real re-run verification gate, Slack alert, incident log, summary |
| `databricks-debug` | Phase 2 — telemetry -> 11-category classification -> two independent `root-cause-analysis` agent runs, reconciled fail-closed on disagreement |
| `testing` | Phase 5 — static syntax/lint/unit-test validation, one bounded retry |
| `pr-review-opsbuddy-fix` | Phase 8 — 7-point Mode A checklist validating the diff against the confirmed root cause |

## Read this before installing: this plugin is not portable like `databricks-job-lineage`

That plugin bundles its own MCP server, so it works in any project once its credentials are set.
**This one doesn't work that way.** `opsbuddy-fix`, `databricks-debug`, and
`pr-review-opsbuddy-fix` all shell out to `workflow/*.py` scripts (git, Jira, Slack, Databricks)
that live specifically in the **ai-sdlc-devops** repo — installing this plugin does not carry
those scripts with it. It only functions when Claude is running with that repo (or one with an
identical `workflow/`/`python/utils/` layout and env vars) open as the current project. `testing`
is the exception — it calls generic tools (`black`, `isort`, `flake8`, `pytest`, `dbt`,
`sqlfluff`) and works in any repo with a similar layout.

If you want a plugin that's genuinely portable to any project the way `databricks-job-lineage`
is, the scripts themselves would need to move into a bundled server inside this plugin instead of
staying as project-local `workflow/*.py` files — a bigger change than packaging what already
exists. This plugin is packaged as-is for **sharing/installing alongside a checkout of
ai-sdlc-devops**, not for standalone use elsewhere.

## Required setup (all already documented in ai-sdlc-devops's own `.env.example`)

- GitHub PAT with `repo` scope, `GITHUB_REPO=owner/repo`
- Jira: `JIRA_OPS_PROJECT_KEY` project must exist with a usable issue type — plus the Atlassian
  MCP connected (this skill's Jira calls prefer that live connector, falling back to
  `workflow/jira_workflow.py`)
- Slack: `SLACK_WEBHOOK_URL` (an incoming webhook for wherever incident alerts should post)
- Databricks: `DATABRICKS_HOST` / `DATABRICKS_TOKEN`, `DATABRICKS_OPS_INCIDENT_TABLE`,
  `OPSBUDDY_VERIFY_ALLOWLIST`

## Differences from the original spec this was built from

- **GitHub**, not Azure DevOps
- **Slack**, not an Email MCP
- Jira via the live **Atlassian MCP** (falls back to `workflow/jira_workflow.py`), not a
  `corp-jira` MCP server
