---
name: opsbuddy-fix
description: >-
  Autonomous, end-to-end incident response for a failed Databricks production job run, across 11
  phases: fetch job telemetry, classify the error into one of 11 standardized categories via the
  databricks-debug sub-skill and the root-cause-analysis (Cat L) agent, create an Ops Jira
  ticket, gate on whether a code fix is genuinely possible, resolve the backing GitHub repo,
  apply and validate the fix, commit and open a pull request, run an automated PR review
  (pr-review-opsbuddy-fix, Mode A) against the confirmed root cause, update the Jira ticket, send
  a Slack incident alert, log the incident to Databricks, and print a final execution summary.
  Use whenever the user gives a Databricks job run ID or job ID and asks to fix, resolve, or
  triage a failure end-to-end (e.g. "job 91004 failed, fix it", "run opsbuddy-fix on run 48213",
  "handle this Databricks incident"). For read-only diagnosis with no fix/PR, use
  databricks-debug directly instead.
---

# opsbuddy-fix — Autonomous Pipeline Failure Monitoring & Fix (11 Phases)

Takes a failed Databricks job run from "it broke" to "here's a reviewed, merged-ready PR and a
logged incident" — maintaining a live checklist across 11 phases. A human still makes the merge
decision; this skill never merges its own PR.

**Portable by design.** Every Databricks call goes through the `opsbuddy-databricks` MCP server
bundled with this plugin — no project-specific script, no assumption about which repo (if any)
is open locally. GitHub and Jira are accessed through whatever MCP connector you already have
for them — see `references/github_mcp_interface.md` and `references/jira_mcp_interface.md` for
the tool shapes expected and how to connect one if you don't have one yet. Slack alerting is a
small bundled script with no dependency on any host project.

Fixes are applied by **fetching and pushing file content through the GitHub MCP server**, not by
cloning the repo locally — there is no guarantee a local checkout exists on whatever machine this
plugin is installed on. See the `testing` sub-skill's two modes for how static validation adapts
to that.

**Argument**: a Databricks job run ID (e.g. `48213`). If only a job ID is known:
```
get_latest_failed_run(job_id="<job-id>")
```

---

## Live Checklist

Display this at the start; reprint with each completed step marked `[x]`.

```
OPSBUDDY-FIX — Run $ARGUMENTS
══════════════════════════════════════
PHASE 0 — PREFLIGHT
  [ ] 0.  Confirm the bundled Databricks MCP server + a GitHub MCP + a Jira MCP are connected

PHASE 1 — TELEMETRY
  [ ] 1.  Get job run details (opsbuddy-databricks MCP server)

PHASE 2 — DIAGNOSE
  [ ] 2.  Classify error & root cause (databricks-debug sub-skill +
          root-cause-analysis (Cat L) agent, adversarial double-check)

PHASE 3 — TICKET
  [ ] 3.  Check for an existing open incident ticket for this run (dedup)
  [ ] 4.  Create Ops Jira ticket (skipped if step 3 found one)
  [ ] 5.  ⛔ GATE 3.5 (automated): Feasibility — CODE_FIX_POSSIBLE

PHASE 4 — RESOLVE REPO
  [ ] 6.  Resolve the GitHub repo backing the failing task's source (get_repo_mapping)

PHASE 5 — REMEDIATION
  [ ] 7.  Read the real source via GitHub, apply the fix
  [ ] 8.  Static validation (testing sub-skill)

PHASE 6 — COMMIT
  [ ] 9.  Commit the fix via the GitHub MCP server (create_or_update_file / push_files)

PHASE 7 — PULL REQUEST
  [ ] 10. Open PR linking hotfix branch → target deployment branch

PHASE 8 — REVIEW
  [ ] 11. Automated PR review (pr-review-opsbuddy-fix, Mode A) vs. root cause
  [ ] 12. ⛔ GATE 8.5 (automated/human): Verify fix against a real re-run

PHASE 9 — TICKET UPDATE
  [ ] 13. Update Jira ticket (PR link, review verdict, execution status)

PHASE 10 — ALERTING & ERROR LOGGING
  [ ] 14. Send Slack incident alert
  [ ] 15. Write incident row to Databricks error log table (log_incident)

PHASE 11 — SUMMARY
  [ ] 16. Print final execution summary
```

---

## Phase 0 — Preflight

Confirm three things are connected before doing anything else:
1. `opsbuddy-databricks` (bundled with this plugin) — if missing, check `DATABRICKS_HOST`/
   `DATABRICKS_TOKEN` are set and the plugin's server actually started.
2. A GitHub MCP — see `references/github_mcp_interface.md`.
3. A Jira MCP — see `references/jira_mcp_interface.md`. If genuinely unavailable, note in the
   final report that ticket steps were skipped, and continue rather than blocking the whole run
   on Jira alone — Jira is important, not load-bearing the way GitHub/Databricks access is.

Stop and say plainly which piece is missing rather than discovering the gap mid-run.

## Phase 1 — Telemetry

```
get_job_run(run_id="$ARGUMENTS")
```
Capture job name, task key, life-cycle/result state, full error message and stack trace
(untruncated), cluster ID, run parameters, run page URL.

## Phase 2 — Diagnose

Invoke the **databricks-debug** sub-skill with the Phase 1 telemetry. It maps the stack trace
into one of 11 standardized error categories (Schema Mismatch, OOM/Executor Lost, Null
Pointer/NoneType, Syntax Error, Permission/Access Denied, Data Not Found at Source, Cluster
Timeout/Startup Failure, Dependency/Library Import Error, Data Skew/Partition Explosion,
Upstream Task Dependency Failure, Infrastructure/Cloud Provider Error) and spawns **two
independent** `root-cause-analysis` (Cat L) agent instances (each given the real source fetched
via `get_source_file`), reconciling them — fail closed to `CODE_FIX_POSSIBLE: false` on
disagreement — into one verdict:
```
ERROR_CATEGORY: <one of the 11 standardized categories>
ROOT_CAUSE_SUMMARY: <2-4 sentences>
CODE_FIX_POSSIBLE: <true|false>
AFFECTED_FILES: <comma-separated repo-relative paths, or "none">
SUGGESTED_FIX_APPROACH: <concrete, minimal, one-paragraph plan>
CONFIDENCE: <high|medium|low>
```
Carry this verdict forward — it drives Gate 3.5, Phase 5, and the Mode A review in Phase 8.

## Phase 3 — Ticket

**Dedup first** — search for an existing open incident referencing this run ID (JQL, via
whatever search tool your Jira MCP exposes — see `references/jira_mcp_interface.md`). If found,
reuse it and skip to whichever phase its state implies (already has a PR → Phase 8). Otherwise
create one, populated with job/run ID, error category, root cause summary, stack trace excerpt,
affected files. Capture the ticket key (e.g. `OPS-42`) — used in every later branch name, commit,
and comment. Confirm the project's actual issue types first (don't assume `Incident` exists —
fall back to `Bug` > `Task` > `Story`).

### ⛔ GATE 3.5 — Feasibility (automated)

- `CODE_FIX_POSSIBLE == true` → proceed to Phase 4.
- `CODE_FIX_POSSIBLE == false` (infra/data-at-source) → **halt**: post a Jira comment explaining
  why this needs manual action, send the Phase 10 Slack alert with
  `EXECUTION_STATUS=MANUAL_ACTION_REQUIRED`, write the Databricks incident row, jump to Phase 11.

## Phase 4 — Resolve the GitHub Repo

```
get_repo_mapping(source_path="<failed task's source_path>", job_id="<job_id>")
```
Always pass `job_id` too — required when the job uses job-level Git source rather than a
Databricks Repo checkout.

- `repo_url` set, `error: null` → continue to Phase 5.
- `error` set → **stop**. There's no git repo to open a PR against (not under `/Repos/`, no
  `git_source` on the job, or a genuine workspace-native notebook). Tell the user plainly
  (include the tool's `error` message) and suggest editing the workspace file directly or wiring
  up a Databricks Repo / job-level Git source, as a manual next step.

## Phase 5 — Remediation & Static Validation

Fetch the actual current file content at `relative_path_in_repo` on `branch` via your GitHub MCP
(a file-contents tool — see `references/github_mcp_interface.md`) — this is the source of truth
you're about to commit against, not the Databricks-side export, which can drift if the
Databricks Repo isn't synced to latest. Read every file in `AFFECTED_FILES` fully (never patch
blind), apply the minimal fix per `SUGGESTED_FIX_APPROACH`. No drive-by refactors or formatting
passes in the same change — the diff should be explainable by the failure alone.

Then invoke the **testing** sub-skill against the changed content for static validation (one
bounded retry on failure). If it still fails after the retry: stop, post a Jira comment, send
the Phase 10 Slack alert with `EXECUTION_STATUS=REMEDIATION_FAILED`, write the Databricks
incident row, jump to Phase 11.

## Phase 6 — Commit

Create a feature branch off the branch resolved in Phase 4 (never assume `main`), named:
```
fix/job-<job_id>-run-<run_id>
```
Commit the fix via your GitHub MCP's create/update-file (or push-files) tool, with a message
referencing the ticket: `OPS-XX: fix <ERROR_CATEGORY> in <job_name>`.

## Phase 7 — Pull Request

Open a PR from the hotfix branch to the base branch resolved in Phase 4. Title:
`Fix: <job_name> (job <job_id>) — <one-line root cause>`. Body must include: what failed (error
message, run URL) and why, what changed and why it fixes it (reference the stack trace line(s)),
the Jira ticket key, and a note that this PR is awaiting the Mode A review + real re-run
verification below before being considered resolved. Capture the PR URL and number.

## Phase 8 — Automated PR Review

Spawn the **pr-review-opsbuddy-fix** skill (Mode A), passing the PR number/repo and the Phase 2
root-cause verdict. It validates the diff against the confirmed root cause via a 7-point
checklist and returns `PASS`/`FAIL`.

- `PASS` → Gate 8.5.
- `FAIL` → loop back to Phase 5 **once** (bounded retry). Fails again → stop, Jira comment, Phase
  10 Slack alert with `EXECUTION_STATUS=REVIEW_FAILED`, Databricks incident row, jump to Phase 11.

### ⛔ GATE 8.5 — Verify Fix Against a Real Re-Run

A Mode A `PASS` is a code review, not proof the job runs now. Re-running a real job can write
real production data from unmerged code, so this is gated on `DATABRICKS_TRIGGER_ALLOWLIST`
(checked by the bundled server's `trigger_job_run` itself — see its docstring):

```
sync_repo(repo_url="<repo_url>", branch="<hotfix branch>")
trigger_job_run(job_id="<job_id>")
```

- Returns `needs_approval: true` → **human-approval gate**: show the fix/PR/what re-running will
  do, wait for explicit approval, then call again with `force="true"`. Declined → proceed with
  `Verified: skipped (not approved)`.
- **Succeeds** → sync the Databricks Repo back to the base branch, proceed to Phase 9.
- **Fails** → loop back to Phase 5 once (same bounded budget as Phase 8's retry), then stop with
  `EXECUTION_STATUS=VERIFICATION_FAILED` if it fails again.
- **One-time `jobs.submit()` run with no `job_id`** → skip this gate, note why in the final
  report.

## Phase 9 — Ticket Update

Comment on the Jira ticket with the PR link, Mode A verdict, and execution status, via your Jira
MCP (see `references/jira_mcp_interface.md`).

## Phase 10 — Alerting & Error Logging

**Slack alert:**
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/slack_alert.py send-incident-summary \
  --jira-id OPS-XX --run-id $ARGUMENTS --category "<ERROR_CATEGORY>" \
  --pr-url <pr_url> --verdict <mode-a-verdict> --status <EXECUTION_STATUS>
```
Requires `SLACK_WEBHOOK_URL` set. If it's not, note in the final report that alerting was
skipped rather than silently failing.

**Error log:**
```
log_incident(record={
  "incident_id": "...", "jira_ticket_id": "OPS-XX", "databricks_job_id": "<job_id>",
  "databricks_run_id": "$ARGUMENTS", "job_name": "...", "task_key": "...",
  "error_category": "<ERROR_CATEGORY>", "root_cause_summary": "...",
  "code_fix_possible": true, "target_repo": "<repo_url>", "branch_name": "...",
  "pr_url": "...", "pr_review_verdict": "...", "execution_status": "<EXECUTION_STATUS>",
  "detected_at": "...", "resolved_at": "..."
})
```
If it returns `error` (table/warehouse not configured), note in the final report rather than
treating it as a hard failure — this is a logging step, not the core deliverable.

## Phase 11 — Summary

```
<✅ | ⚠️> OPS-XX — <EXECUTION_STATUS>
══════════════════════════════════════
  Job/Run      : <job_name> / $ARGUMENTS
  Category     : <ERROR_CATEGORY>
  Repo/Branch  : <repo_url> / <branch>
  PR           : <pr_url>
  Review       : <PASS/FAIL>
  Verified     : <PASS/FAIL/skipped (one-time run)/skipped (not approved)>
  Jira         : <status>
  Slack sent   : <yes/no>
  Databricks row: <incident_id/skipped>
══════════════════════════════════════
```
If the run halted at Gate 3.5, Phase 5, Phase 8, or Gate 8.5, state clearly which phase it
stopped at and what manual action is now required.
