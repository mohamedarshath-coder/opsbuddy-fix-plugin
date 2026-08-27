---
name: databricks-debug
description: >-
  Diagnoses a failed Databricks job run: fetches telemetry, classifies the failure into one of
  11 standardized error categories, and spawns two independent root-cause-analysis (Cat L) agent
  instances to adversarially confirm whether a code fix is genuinely possible before anything
  gets changed. Use standalone for ad-hoc triage of a Databricks job failure ("job 91004 failed,
  what's wrong with it", "diagnose run 48213"), or invoked as Phase 2 of opsbuddy-fix with
  telemetry already fetched. Read-only — never applies a fix or opens a PR itself.
---

# databricks-debug

**Argument**: a Databricks job run ID. Used standalone for ad-hoc triage, or invoked from
opsbuddy-fix Phase 2 with telemetry already fetched (skip Step 1 in that case).

Fully portable — everything here runs through the `opsbuddy-databricks` MCP server bundled with
this plugin. No project-specific scripts, no assumption about which repo (if any) is open
locally.

## Step 0 — Confirm the bundled MCP server is connected

Look for `opsbuddy-databricks` in your connected MCP servers. If it's not there, stop and say so
plainly rather than guessing at telemetry — check `DATABRICKS_HOST`/`DATABRICKS_TOKEN` are set
and the plugin's server actually started.

---

## Step 1 — Gather Telemetry

```
get_job_run(run_id="$ARGUMENTS")
```
If you were only given a job ID, resolve the latest failed run first:
```
get_latest_failed_run(job_id="<job_id>")
```
Then fetch the failing task's actual source so Step 3's agents have real code to reason over,
not just the error message:
```
get_source_file(path="<source_path from get_job_run's failed task>")
```
Capture the full stack trace, error message, cluster ID, task parameters, and the fetched source
content — do not truncate.

---

## Step 2 — Classify the Error

| Error category | How to identify | Typically `CODE_FIX_POSSIBLE` |
|---|---|---|
| Schema Mismatch | `AnalysisException`, `cannot resolve column`, schema evolution errors | true |
| Out-of-Memory (OOM) / Executor Lost | `ExecutorLostFailure`, `java.lang.OutOfMemoryError`, `Container killed by YARN` | false (unless caused by an obvious unbounded collect/join in code) |
| Null Pointer / NoneType | `NullPointerException`, `NoneType has no attribute` | true |
| Syntax Error | `SyntaxError`, `IndentationError`, `ParseException` | true |
| Permission / Access Denied | `AccessDeniedException`, `PERMISSION_DENIED`, `403` | false |
| Data Not Found at Source | `FileNotFoundException`, `Path does not exist`, empty source partition | false |
| Cluster Timeout / Startup Failure | `Cluster did not start`, `INSTANCE_UNREACHABLE`, spot eviction | false |
| Dependency / Library Import Error | `ModuleNotFoundError`, `ImportError`, library install failure | true |
| Data Skew / Partition Explosion | task duration outliers, `TooManyPartitionsException` | true (if code-level partitioning fix applies) |
| Upstream Task Dependency Failure | task `state.result_state == UPSTREAM_FAILED` | false (fix belongs in the upstream job) |
| Infrastructure / Cloud Provider Error | `InternalError`, cloud provider 5xx, network errors | false |

Pick the single best-matching category from the stack trace signature. This is a
**preliminary** classification — Step 3's agents may confirm or override it.

---

## Step 3 — Invoke root-cause-analysis (Cat L) — Adversarial Double-Check

`CODE_FIX_POSSIBLE` gates whether opsbuddy-fix is allowed to push a code change — one LLM
judgment call is not enough of a guardrail. Spawn **two independent instances** of the
`root-cause-analysis` subagent (Cat L) in parallel, each given identical input (full stack
trace, error message, task parameters, the Step 1 fetched source content, and the Step 2
preliminary category and guess) but **no visibility into each other's answer**. Neither agent
has local filesystem access to the target repo — the fetched source text you pass in is all it
has to work with.

Each returns:
```
ERROR_CATEGORY: <one of the 11 standardized categories>
ROOT_CAUSE_SUMMARY: <2-4 sentences>
CODE_FIX_POSSIBLE: <true|false>
AFFECTED_FILES: <comma-separated repo-relative paths, or "none">
SUGGESTED_FIX_APPROACH: <concrete, minimal, one-paragraph plan>
CONFIDENCE: <high|medium|low>
```

**Reconcile:**

| Agreement | Action |
|---|---|
| Both `true`, same category | Proceed with either verdict's `AFFECTED_FILES`/`SUGGESTED_FIX_APPROACH` (prefer higher `CONFIDENCE` if they differ in detail) |
| Both `false` | Proceed to halt — agreement on "not fixable" is just as actionable as agreement on "fixable" |
| **Disagree** on `CODE_FIX_POSSIBLE` | **Fail closed**: treat as `false`. Surface both verdicts verbatim so a human sees exactly where they diverged. Never average, guess, or pick one arbitrarily. |
| Either reports `CONFIDENCE: low` | Surface that explicitly regardless of agreement |

---

## Step 4 — Report

Return the reconciled verdict block (or both verdicts plus the "disagree → fail closed" note),
plus a plain-English one-paragraph root-cause summary. If invoked standalone, also state whether
a manual fix or `opsbuddy-fix $ARGUMENTS` is the appropriate next step.
