---
name: opsbuddy-fix
description: >-
  End-to-end autonomous incident response for a failed Databricks job: diagnoses the failure,
  classifies it into one of 11 standardized error categories, decides whether a code fix is
  genuinely possible, and — only when it is — creates a branch, applies a minimal fix, opens a
  pull request, reviews that PR against the confirmed root cause, and (for jobs explicitly
  approved for it) re-runs the job for real to prove the fix works before calling it resolved.
  Use whenever the user gives a Databricks job ID or run ID and asks to fix it, resolve it, open
  a PR for the failure, "opsbuddy-fix this", or wants the full failure-to-reviewed-PR flow rather
  than just a diagnosis (e.g. "job 91004 failed, fix it", "open a PR for this failure", "run
  opsbuddy-fix on run 48213"). For read-only investigation with no PR opened, use
  databricks-job-lineage instead — this skill leans on the same MCP tools for the diagnostic
  legwork and adds the decide/fix/PR/verify half on top. This is a Desktop/MCP-only adaptation of
  the full opsbuddy-fix pipeline: it has no shell access (static validation is a careful reasoning
  pass over the diff, not a real linter/test run) and no Jira MCP is connected in this
  environment (ticket creation/updates are surfaced as text for the user to paste in manually,
  never silently skipped).
---

# opsbuddy-fix

Takes a failed Databricks job from "it broke" to "here's a reviewed, verified pull request" —
without ever merging anything itself. A human always makes the merge decision.

This is adapted from a fuller version of opsbuddy-fix that also runs in Claude Code (which has
shell access, a Jira integration, and can spawn independent parallel subagents). Two safety
mechanics from that version are weakened here by real platform constraints, and both are called
out explicitly at the step where they apply rather than silently glossed over:

- **Root-cause double-check**: the full version uses two independent subagents and fails closed
  on disagreement. This version does one diagnosis pass, then a second explicit self-critique
  pass in the same conversation that tries to refute the first pass's conclusion — real, but not
  truly independent.
- **Static validation**: the full version runs actual linters and tests. This version can only
  reason carefully about the diff for obvious syntax/style problems — it cannot execute anything.

Neither of those is a reason to skip the checks; they're a reason to be honest in the final
report about how much confidence each one actually earned.

## Step 0 — Confirm the MCP servers are connected

This skill needs tools from **two** MCP servers in the same session:

- `opsbuddy-databricks-lineage`: `get_latest_failed_run`, `get_job_run`, `get_job_config`,
  `get_source_file`, `get_job_orchestration`, `sync_repo`, `trigger_job_run`
- `github`: `create_branch`, `get_file_contents`, `create_or_update_file`,
  `create_pull_request`, `get_pull_request_files`, `list_pull_requests`,
  `create_pull_request_review`

If either server isn't connected, or `sync_repo`/`trigger_job_run` specifically aren't present
(an older version of the Databricks server may not have them yet), stop and say so plainly —
don't attempt a partial run or improvise a fix without being able to verify it.

There is no Jira MCP connected in this environment. Every step below that would normally touch
Jira instead produces ready-to-paste text for the user and says so explicitly — never silently
skipped, never pretended to have happened.

## Step 1 — Resolve the target run and gather telemetry

- If given a run ID directly, use it as-is. If only a job ID, call `get_latest_failed_run(job_id)`
  — if it returns no run, say so and stop.
- Call `get_job_run(run_id)`. Capture the failed task's `error_message` and full `stack_trace`
  verbatim, the `job_id`, `task_key`, and `run_page_url`.
- If the run's `result_state` isn't actually a failure (e.g. `SUCCESS`, or `life_cycle_state` is
  still `RUNNING`), **stop here** — there is nothing to fix. Report that plainly rather than
  inventing an incident. Re-check with a fresh `get_job_run` call if the user insists it should
  be failed; don't assume stale data without looking.
- Note the job's actual source: call `get_job_config(job_id)` and `get_source_file(source_path)`
  for the failed task. If `source_path` isn't under `/Repos/...`, this job isn't backed by a
  GitHub-synced checkout — the git-side steps below (branch/PR/sync_repo) won't apply, and this
  should be flagged as a case for manual/`databricks-job-lineage`-only triage instead.

## Step 2 — Classify and diagnose (with self-critique)

**First pass.** Using the telemetry from Step 1 and the real source from `get_source_file`,
classify the failure into one of the 11 standardized categories (Schema Mismatch, OOM/Executor
Lost, Null Pointer/NoneType, Syntax Error, Permission/Access Denied, Data Not Found at Source,
Cluster Timeout/Startup Failure, Dependency/Library Import Error, Data Skew/Partition Explosion,
Upstream Task Dependency Failure, Infrastructure/Cloud Provider Error), and produce a draft
verdict:

```
ERROR_CATEGORY: <category>
ROOT_CAUSE_SUMMARY: <2-4 sentences, grounded in the actual code read via get_source_file>
CODE_FIX_POSSIBLE: <true|false>
AFFECTED_FILES: <repo-relative paths, or "none">
SUGGESTED_FIX_APPROACH: <concrete, minimal>
CONFIDENCE: <high|medium|low>
```

**Second pass — self-critique.** Before trusting the draft, deliberately try to refute it:
- If `CODE_FIX_POSSIBLE: true` — argue the *opposite* case as hard as you genuinely can. Is this
  actually an infra/permissions/upstream-data problem wearing a code-shaped symptom? Would the
  suggested fix actually address the root cause, or just the immediate crash line?
- If the self-critique finds a real flaw, revise the verdict (don't just note the objection and
  ignore it) and lower `CONFIDENCE` to reflect the uncertainty that surfaced.
- If the self-critique fails to find a genuine flaw, keep the verdict as-is — don't manufacture
  doubt for its own sake.

Report the **final, post-critique** verdict, and mention explicitly that this was a single-pass
self-critique, not independent adversarial review, so the user calibrates trust accordingly.

## Step 3 — Feasibility gate (automated)

- **`CODE_FIX_POSSIBLE: true`** → continue to Step 4.
- **`CODE_FIX_POSSIBLE: false`** → stop here. Produce this text block for the user to act on
  manually (paste into Jira, Slack, wherever it needs to go):
  ```
  MANUAL ACTION REQUIRED — <job_name> run <run_id>
  Category: <ERROR_CATEGORY>
  Root cause: <ROOT_CAUSE_SUMMARY>
  Run page: <run_page_url>
  This cannot be resolved with a code change in this repo — see root cause above.
  ```
  Do not proceed to Step 4.

## Step 4 — Check for an existing PR (best-effort dedup)

No Jira means no reliable dedup key. As a best-effort substitute, call `list_pull_requests`
(state=`open`) on the target repo and check whether one already references this run ID or job
in its title. If one does, stop and point the user at it instead of opening a duplicate.

## Step 5 — Create the hotfix branch

Call `create_branch` with `owner`, `repo`, a branch name like `hotfix-<short-slug>-<run_id>`,
and `from_branch` set to the repo's actual default branch (don't assume `main` — confirm it, e.g.
from a prior `get_file_contents` call or by asking if unknown).

## Step 6 — Apply the fix

For each file in `AFFECTED_FILES`: call `get_file_contents` to get the current content and its
`sha` (needed later to update it). Read the real content fully before editing — never patch
blind from the stack trace alone. Apply the minimal fix from `SUGGESTED_FIX_APPROACH`. Do not
refactor beyond it or touch files outside `AFFECTED_FILES` without a stated reason.

**Static validation (adapted — no execution available).** Re-read the new content once,
specifically checking for: unbalanced brackets/quotes, obvious indentation errors (Python is
whitespace-sensitive), a name used before it's defined, and whether the fix could plausibly still
raise the same error class under a slightly different input. This is a reasoning pass, not a
linter or test run — say so in the final report rather than implying it was verified execution.

## Step 7 — Push the fix

Call `create_or_update_file` for each changed file: `owner`, `repo`, `path`, the new `content`,
a commit `message` (e.g. `Fix <ERROR_CATEGORY> in <job_name>`), the hotfix `branch`, and the
`sha` captured in Step 6 (required — omitting it when updating an existing file will fail).

## Step 8 — Open the PR

Call `create_pull_request`: `head` = the hotfix branch, `base` = the branch from Step 5, a title
referencing the job/run, and a body containing the full verdict block from Step 2.

## Step 9 — Review the PR (Mode A)

Call `get_pull_request_files` on the new PR and check the diff against this checklist:

1. **Scope** — only files in `AFFECTED_FILES` are touched.
2. **Targeted** — the specific line(s) named in `ROOT_CAUSE_SUMMARY` are what actually changed.
3. **Category match** — the fix addresses the classified category, not just the crash symptom.
4. **No suppression** — no bare `except`, no silently dropping/nulling bad data, unless justified
   with an explicit comment.
5. **Static validation** — the Step 6 reasoning pass found no new issues.
6. **No scope creep** — no unrelated changes.
7. **Re-run safety** — re-running the job after this fix can't duplicate data or cause harm if
   run twice.

Report `PASS` or `FAIL` with a one-line note per item. On `FAIL`, go back to Step 6 **once**
with the specific objection as context, then re-push (Step 7) and re-review. If it fails again,
stop and report both failures plainly rather than looping indefinitely.

Optionally, call `create_pull_request_review` to post this checklist as an actual review comment
on the PR (event=`COMMENT`, not `APPROVE` — approval is a human decision).

## Step 10 — Verify the fix for real (Gate — allowlist or human approval)

A `PASS` in Step 9 is a code review, not proof the job runs now. Re-running a real job can
execute code that writes real data from an unmerged branch, so this step is gated:

```
sync_repo(repo_url, branch=<hotfix branch>)
trigger_job_run(job_id=<job_id>)
```

- If `trigger_job_run` returns `needs_approval: true` — this job isn't on the server's
  `DATABRICKS_TRIGGER_ALLOWLIST`. **Stop and ask the user explicitly**: show them the fix, the
  PR, and exactly what re-running will do (which job, whether it writes data, to what). Only if
  they approve, call `trigger_job_run` again with `force="true"`. If they decline, proceed to
  Step 11 with `Verified: skipped (not approved)` and say so plainly.
- **On success** — call `sync_repo` again to point the Repo back at the base branch (don't leave
  a real job pointed at an unmerged branch longer than necessary), then proceed to Step 11 with
  `Verified: PASS`.
- **On failure** — the Step 9 review was wrong. Go back to Step 6 **once** (same bounded-retry
  budget as Step 9 — don't reset the counter) with the new failure's telemetry. If it fails
  again, stop and report both failures, `Verified: FAIL`.

## Step 11 — Final report

Since there's no Jira MCP, produce the ticket text as well as the run summary — both ready to
paste, both clearly labeled as **not yet posted anywhere**:

```markdown
# opsbuddy-fix report — <job_name> (job <job_id>, run <run_id>)

## Outcome
<RESOLVED | MANUAL_ACTION_REQUIRED | REVIEW_FAILED | VERIFICATION_FAILED | skipped-not-approved>

## Diagnosis
<final verdict block from Step 2, noting it was single-pass self-critique>

## What was done
- Branch: <branch>
- PR: <pr_url>
- Mode A review: <PASS/FAIL, with notes>
- Static validation: reasoning pass only, not executed — <what it checked>
- Real re-run verification: <PASS/FAIL/skipped, with reason>

## Jira — not connected in this environment
Paste this into the ticket tracker manually:
> [opsbuddy-fix] <job_name> run <run_id> failed — <ERROR_CATEGORY>. Root cause: <summary>.
> PR: <pr_url>. Review: <verdict>. Verified: <verdict>.
```

Deliver this as a file, not just chat text, for the same reason `databricks-job-lineage` does —
it gets pasted elsewhere and re-read later.

## Handling gaps

Same discipline as `databricks-job-lineage`: a tool returning an empty/negative result (no PR
found, review passed cleanly) is a normal finding — report it as such. A tool call *failing*
(timeout, permission denied, `sync_repo` can't find the Repo) is a gap in what you could verify,
not evidence that everything's fine — flag it explicitly and don't let a failed check quietly
read as a passed one.
