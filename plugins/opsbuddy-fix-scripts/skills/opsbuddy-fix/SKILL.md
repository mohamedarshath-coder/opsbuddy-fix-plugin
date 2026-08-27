---
name: opsbuddy-fix
description: >-
  Autonomous, end-to-end incident response for a failed Databricks production job run, across 11
  phases: fetch job telemetry, classify the error into one of 11 standardized categories via the
  databricks-debug sub-skill and the root-cause-analysis (Cat L) agent, create an Ops Jira
  ticket, gate on whether a code fix is genuinely possible, clone the repo and create an isolated
  hotfix branch, apply and statically validate the fix (testing sub-skill), commit and push to
  GitHub, open a pull request, run an automated PR review (pr-review-opsbuddy-fix, Mode A)
  against the confirmed root cause, update the Jira ticket, send a Slack incident alert, log the
  incident to Databricks, and print a final execution summary. Use whenever the user gives a
  Databricks job run ID or job ID and asks to fix, resolve, or triage a failure end-to-end (e.g.
  "job 91004 failed, fix it", "run opsbuddy-fix on run 48213", "handle this Databricks
  incident"). For read-only diagnosis with no fix/PR, use databricks-debug directly instead.
---

# opsbuddy-fix — Autonomous Pipeline Failure Monitoring & Fix (11 Phases)

Takes a failed Databricks job run from "it broke" to "here's a reviewed, merged-ready PR and a
logged incident" — maintaining a live 18-step checklist across 11 phases. A human still makes
the merge decision; this skill never merges its own PR.

**Not project-portable.** Unlike a plugin that bundles its own MCP server, every step below
calls `workflow/*.py` scripts (git, Jira, Slack, Databricks) that live in a specific repo — the
one this skill was authored against. Installing this plugin does not carry those scripts with
it: it only works when Claude is running with that repo (or one with the identical
`workflow/`/`python/utils/` layout and env vars) as the current project.

**Argument**: a Databricks job run ID (e.g. `48213`). If only a job ID is known, resolve the
latest failed run first:
```bash
python workflow/databricks_workflow.py get-latest-failed-run --job-id <job-id>
```

Every phase below pairs an MCP-preferred call with the real, tested-today script fallback. This
repo does not have Azure DevOps or a dedicated Email MCP — Phase 6 pushes to **GitHub** and
Phase 10 alerts via **Slack**, not Azure/Email, per this project's actual infrastructure.

---

## Live Checklist

Display this at the start; reprint with each completed step marked `[x]`.

```
OPSBUDDY-FIX — Run $ARGUMENTS
══════════════════════════════════════
PHASE 0 — PREFLIGHT
  [ ] 0.  Verify GitHub + Jira access before starting

PHASE 1 — TELEMETRY
  [ ] 1.  Get job run details (opsbuddy_mcp / databricks_workflow.py)

PHASE 2 — DIAGNOSE
  [ ] 2.  Classify error & root cause (databricks-debug sub-skill +
          root-cause-analysis (Cat L) agent, adversarial double-check)

PHASE 3 — TICKET
  [ ] 3.  Check for an existing open incident ticket for this run (dedup)
  [ ] 4.  Create Ops Jira ticket (skipped if step 3 found one)
  [ ] 5.  ⛔ GATE 3.5 (automated): Feasibility — CODE_FIX_POSSIBLE

PHASE 4 — GIT SETUP
  [ ] 6.  Clone target repo + create isolated hotfix branch

PHASE 5 — REMEDIATION
  [ ] 7.  Apply code fix
  [ ] 8.  Static validation (testing sub-skill)

PHASE 6 — COMMIT & PUSH
  [ ] 9.  Commit (standard message convention) + push to GitHub

PHASE 7 — PULL REQUEST
  [ ] 10. Open PR linking hotfix branch → target deployment branch

PHASE 8 — REVIEW
  [ ] 11. Automated PR review (pr-review-opsbuddy-fix, Mode A) vs. root cause
  [ ] 12. ⛔ GATE 8.5 (automated/human): Verify fix against a real re-run

PHASE 9 — TICKET UPDATE
  [ ] 13. Update Jira ticket (PR link, review verdict, execution status)

PHASE 10 — ALERTING & ERROR LOGGING
  [ ] 14. Send Slack incident alert
  [ ] 15. Write incident row to Databricks error log table

PHASE 11 — SUMMARY
  [ ] 16. Clean up local working clone
  [ ] 17. Print final execution summary
```

---

## Phase 0 — Preflight

```bash
python workflow/git_workflow.py check-access
python workflow/jira_workflow.py check-access --project OPS
```
Stop here on any failure — do not discover a permission gap mid-run.

## Phase 1 — Telemetry

Fetches complete job telemetry, runtime parameters, stack traces, and cluster logs:
```
# Preferred — opsbuddy_mcp (not yet registered; placeholder for when it is)
mcp__opsbuddy_mcp__getJobRunDetails(run_id="$ARGUMENTS")

# Fallback — real, tested today
python workflow/databricks_workflow.py get-run-failure --run-id $ARGUMENTS
```
Capture job name, task key, life-cycle/result state, full error message and stack trace
(untruncated), cluster ID, run parameters, run page URL.

## Phase 2 — Diagnose

Invoke the **databricks-debug** sub-skill with the Phase 1 telemetry. It maps the stack trace
into one of 11 standardized error categories (Schema Mismatch, OOM/Executor Lost, Null
Pointer/NoneType, Syntax Error, Permission/Access Denied, Data Not Found at Source, Cluster
Timeout/Startup Failure, Dependency/Library Import Error, Data Skew/Partition Explosion,
Upstream Task Dependency Failure, Infrastructure/Cloud Provider Error) and spawns **two
independent** `root-cause-analysis` (Cat L) agent instances, reconciling them — fail closed to
`CODE_FIX_POSSIBLE: false` on disagreement (see the databricks-debug skill's reconciliation
table) — into one verdict:
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

**Dedup first:**
```bash
python workflow/jira_workflow.py find-incident --project OPS --run-id $ARGUMENTS
```
If found, reuse that ticket and skip to whichever phase its state implies. Otherwise create one:
```
# Preferred — Atlassian MCP (this repo's live corp-jira equivalent)
mcp__claude_ai_Atlassian__createJiraIssue(
  cloudId="0a282cc9-b92e-4df9-97e8-24c07fcc7cec", projectKey="OPS", issueTypeName="Incident",
  summary="[opsbuddy-fix] <job_name> run $ARGUMENTS failed — <ERROR_CATEGORY>",
  description="<run metadata + full diagnostics markdown>"
)

# Fallback
python workflow/jira_workflow.py create --project OPS --type Incident \
  --summary "[opsbuddy-fix] <job_name> run $ARGUMENTS failed — <ERROR_CATEGORY>" \
  --description "<run metadata + full diagnostics markdown>" --priority High --label opsbuddy-fix
```
Populate with job/run ID, error category, root cause summary, stack trace excerpt, affected
files. Capture the ticket key (e.g. `OPS-42`) — used in every later branch name, commit, comment.

### ⛔ GATE 3.5 — Feasibility (automated)

- `CODE_FIX_POSSIBLE == true` → proceed to Phase 4.
- `CODE_FIX_POSSIBLE == false` (infra/data-at-source) → **halt**: post a Jira comment explaining
  why this needs manual action, send the Phase 10 Slack alert with
  `EXECUTION_STATUS=MANUAL_ACTION_REQUIRED`, write the Databricks incident row, jump to Phase 11.

## Phase 4 — Git Setup

Clones the target repository and creates an isolated hotfix branch:
```bash
python workflow/git_workflow.py clone --repo-url https://github.com/$GITHUB_REPO.git \
  --target-dir tmp/opsbuddy-fix/OPS-XX
python workflow/git_workflow.py create-branch --repo-dir tmp/opsbuddy-fix/OPS-XX \
  --branch OPS-XX/hotfix-<slug-from-error-category> --base main
```

## Phase 5 — Remediation & Static Validation

Read every file in `AFFECTED_FILES` fully (never patch blind), apply the minimal fix per
`SUGGESTED_FIX_APPROACH`. Then invoke the **testing** sub-skill against the changed files for
local static syntax/logic verification (one bounded retry on failure). If it still fails after
the retry: stop, post a Jira comment, send the Phase 10 Slack alert with
`EXECUTION_STATUS=REMEDIATION_FAILED`, write the Databricks incident row, jump to Phase 11.

## Phase 6 — Commit & Push

Commits with a standard message convention and pushes to **GitHub**:
```bash
python workflow/git_workflow.py commit --repo-dir tmp/opsbuddy-fix/OPS-XX \
  --message "OPS-XX: fix <ERROR_CATEGORY> in <job_name>" --files <path1>,<path2>,...
python workflow/git_workflow.py push --repo-dir tmp/opsbuddy-fix/OPS-XX \
  --branch OPS-XX/hotfix-<slug>
```

## Phase 7 — Pull Request

Generates an automated PR linking the hotfix branch to the target deployment branch:
```bash
cd tmp/opsbuddy-fix/OPS-XX && python ../../../workflow/git_workflow.py create-pr \
  --branch OPS-XX/hotfix-<slug> --jira-id OPS-XX --base main
```
Auto-links the Jira ticket and transitions it to "In Review". Capture the PR URL and number.

## Phase 8 — Automated PR Review

Spawn the **pr-review-opsbuddy-fix** skill (Mode A), passing the PR number and the Phase 2
root-cause verdict. It validates the diff against the confirmed root cause via a 7-point
checklist and returns `PASS`/`FAIL`.

- `PASS` → Gate 8.5.
- `FAIL` → loop back to Phase 5 **once** (bounded retry). Fails again → stop, Jira comment, Phase
  10 Slack alert with `EXECUTION_STATUS=REVIEW_FAILED`, Databricks incident row, jump to Phase 11.

### ⛔ GATE 8.5 — Verify Fix Against a Real Re-Run

A Mode A `PASS` is a code review, not proof the job runs now. Re-running a real job can write
real production data from unmerged code, so this is gated:
```bash
python -c "from workflow.databricks_workflow import is_verify_allowed; print(is_verify_allowed(<job_id>))"
```
- `True` (job on `OPSBUDDY_VERIFY_ALLOWLIST`) → proceed automatically.
- `False` → **human-approval gate**: show the fix/PR/what re-running will do, wait for explicit
  approval before `trigger-and-wait --force`. Declined → proceed with `Verified: skipped (not
  approved)`.

Once allowed:
```bash
python workflow/databricks_workflow.py sync-repo --repo-url https://github.com/$GITHUB_REPO.git \
  --branch OPS-XX/hotfix-<slug>
python workflow/databricks_workflow.py trigger-and-wait --job-id <job_id> --timeout 600
```
Succeeds → Phase 9. Fails → loop back to Phase 5 once (same bounded budget as Phase 8's retry),
then stop with `EXECUTION_STATUS=VERIFICATION_FAILED` if it fails again. One-time `jobs.submit()`
run with no `job_id` → skip this gate and note why in the final report. After a successful
re-run, sync the Databricks Repo back to `main`.

## Phase 9 — Ticket Update

```
# Preferred — Atlassian MCP
mcp__claude_ai_Atlassian__addCommentToJiraIssue(cloudId="0a282cc9-b92e-4df9-97e8-24c07fcc7cec",
  issueIdOrKey="OPS-XX",
  body="opsbuddy-fix: PR <pr_url> opened and passed Mode A review (<verdict>). Status: <EXECUTION_STATUS>.")

# Fallback
python workflow/jira_workflow.py comment-rich OPS-XX \
  "opsbuddy-fix: PR opened and passed Mode A review (<verdict>). Status: <EXECUTION_STATUS>." \
  --link pr=<pr_url>
```

## Phase 10 — Alerting & Error Logging

**Slack alert** (this repo's real alerting channel — no Email MCP is configured here):
```
# Preferred — Slack MCP (not yet registered; placeholder for when it is)
mcp__Slack_MCP__postMessage(channel="$SLACK_INCIDENT_CHANNEL", text="...", blocks=[...])

# Fallback — real, tested today
python workflow/slack_workflow.py send-incident-summary \
  --jira-id OPS-XX --run-id $ARGUMENTS --category "<ERROR_CATEGORY>" \
  --pr-url <pr_url> --verdict <mode-a-verdict> --status <EXECUTION_STATUS>
```

**Error log** — write a structured incident row into Databricks:
```bash
python workflow/databricks_workflow.py log-incident --json-file <path-to-record.json>
```
Record fields: incident_id, jira_ticket_id, databricks_job_id/run_id, job_name, task_key,
error_category, root_cause_summary, stack_trace_excerpt, code_fix_possible, target_repo,
branch_name, commit_sha, pr_url, pr_review_verdict, execution_status, severity, detected_at,
resolved_at, slack_sent.

## Phase 11 — Summary

Clean up the isolated clone (`rm -rf tmp/opsbuddy-fix/OPS-XX`, skip if a retry might still need
it), then print the final execution summary:
```
<✅ | ⚠️> OPS-XX — <EXECUTION_STATUS>
══════════════════════════════════════
  Job/Run      : <job_name> / $ARGUMENTS
  Category     : <ERROR_CATEGORY>
  Branch       : <branch>
  PR           : <pr_url>
  Review       : <PASS/FAIL>
  Verified     : <PASS/FAIL/skipped (one-time run)/skipped (not approved)>
  Jira         : <status>
  Slack sent   : <yes/no>
  Databricks row: <incident_id>
══════════════════════════════════════
```
If the run halted at Gate 3.5, Phase 5, Phase 8, or Gate 8.5, state clearly which phase it
stopped at and what manual action is now required.
