---
name: databricks-job-lineage
description: >-
  Traces the full lineage behind a Databricks job or run given just a job ID or run ID: fetches
  job/run telemetry and the exact error, the python files and notebooks each task actually
  executes, the task dependency graph (which tasks feed the one that failed and which depend on
  it), job-to-job orchestration (what triggers this job, what it triggers downstream), and Unity
  Catalog table lineage (upstream tables read, downstream tables and jobs affected) — assembled
  into one markdown lineage report. Use whenever the user gives a Databricks job ID or run ID and
  asks to trace lineage, find what's upstream or downstream of a failure, see which notebook or
  python file is actually involved, understand the blast radius of a broken pipeline, or wants
  the full picture of a job failure before deciding what to do about it (e.g. "job 91004 failed,
  trace the lineage", "what feeds into run 48213", "show me everything upstream and downstream
  of this failing job", "which notebook does job 12345 run and what does it write to"). This is a
  read-only diagnostic skill — it never opens PRs, files tickets, or attempts a fix. For
  end-to-end remediation, use opsbuddy-fix instead; this skill is a good input to feed into it or
  into a human triage conversation.
---

# Databricks Job Lineage

Given a job ID (or a specific run ID), this skill answers one question thoroughly: **everything
connected to this failure, in every direction.** Not just "what error did it throw," but what
code ran, what fed into that code, what depended on its output, and what else in the workspace
touches the same data. That context is what turns "job 91004 failed" into "job 91004 failed in
`transform_orders.py` because the `raw_orders` table it reads was written by job 88213, which
itself failed silently three hours earlier" — the kind of thing that takes a person 20 minutes of
clicking through the Databricks UI to piece together by hand.

This skill is deliberately narrow: it traces and reports. It does not fix anything, file
anything, or notify anyone — that keeps it safe to run freely and fast to run often, and it means
the report it produces is exactly the kind of grounded, cited context a human (or opsbuddy-fix)
needs before deciding what to do next.

## Step 0 — Confirm the MCP server is connected

This skill only ever talks to Databricks through the six MCP tools described in
`references/mcp_interface.md` (`get_latest_failed_run`, `get_job_run`, `get_job_config`,
`get_source_file`, `get_job_orchestration`, `get_table_lineage`). There's no REST or CLI fallback
by design — a fallback path tends to silently mask an MCP that's misconfigured or not actually
connected, and lineage tracing is exactly the kind of task where partial, unlabeled data is worse
than an honest "I can't reach this."

Before doing anything else, check whether tools matching that contract are available. If they
aren't, stop and tell the user plainly that their Databricks MCP server isn't connected in this
session, and point them at `references/mcp_interface.md` if they're the one building it. Don't
attempt to improvise the data from general knowledge of Databricks — a wrong guess about which
table a job reads is worse than no answer.

## Step 1 — Resolve the target run

- If the user gave a run ID directly, use it as-is.
- If they gave only a job ID, call `get_latest_failed_run(job_id)`. If it returns no run, say so
  and stop — there's nothing to trace.
- If the user says the job is currently succeeding and they want lineage for a specific past
  incident, ask which run before guessing.

## Step 2 — Pull run telemetry and the task graph

Call `get_job_run(run_id)` and `get_job_config(job_id)` in parallel — they answer different
questions (what happened this run vs. how tasks relate structurally) and neither depends on the
other.

From `get_job_run`, find the task(s) in a failed state and capture their `error_message` and full
`stack_trace` verbatim — don't summarize or truncate it in your own working notes, since you'll
want the exact text later to correlate against source lines.

From `get_job_config`, walk the `depends_on` edges to find, relative to the failed task:
- **Upstream tasks**: everything the failed task (transitively) depends on.
- **Downstream tasks**: everything that (transitively) depends on the failed task.

For a wide DAG, one hop in each direction is usually enough to explain the failure; go further
only if the immediate neighbors don't tell a complete story (e.g. the failed task's direct
predecessor also failed, and you need to know why *that* happened).

## Step 3 — Read the actual code

For the failed task, and for any upstream/downstream task that seems relevant to the story, call
`get_source_file(source_path)`. When the stack trace names a line number, quote the few lines of
real source around it rather than describing the file in the abstract — that's the difference
between a lineage report someone can act on and one they have to go re-verify themselves.

If a source file comes back unavailable, note it as a gap in the report (see "Handling gaps"
below) rather than skipping it silently.

## Step 4 — Job-to-job orchestration

Call `get_job_orchestration(job_id)`. This tells you what triggers this job and what this job
triggers — a different layer from the task DAG in Step 2, which only covers tasks *inside* this
one job. Empty lists here are common and fine; report them as "no upstream/downstream jobs
found," which is itself useful information (it rules out "some other job caused this").

## Step 5 — Data lineage

Call `get_table_lineage(run_id)`. Report the tables the failed run's task(s) read from and wrote
to, and anything in `downstream_consumers` — that's the blast radius: what else in the workspace
will see stale or missing data because this run failed. If the failed task never got far enough
to read or write anything, say that plainly rather than implying a lineage that doesn't exist.

## Handling gaps

Two different things can go wrong, and the report should never blur them together:

- **A tool returns an empty result** (no downstream jobs, no tables touched, run never started).
  This is a fact about the system — report it as a normal finding.
- **A tool call itself fails or errors** (timeout, permission denied, path not found). This is a
  gap in your visibility, not evidence of absence. Flag it explicitly in the report (e.g. "table
  lineage unavailable for this run — Unity Catalog lineage API returned an error") so nobody
  mistakes "I couldn't check" for "there's nothing there."

## Report structure

Write the finished trace as a markdown file (not just chat text — this is the kind of report
someone pastes into a ticket or reads later) using this structure:

```markdown
# Lineage Report — <job_name> (job <job_id>, run <run_id>)

## Failure summary
What failed, when, the exact error message, and the run page URL. One or two sentences of plain
context, not a restatement of the stack trace.

## Task dependency graph
The failed task's immediate upstream and downstream tasks within this job, and whether each
succeeded, failed, or didn't run. A short indented list or a mermaid `graph TD` block both work —
pick whichever makes a wide DAG easier to scan.

## Source involved
For the failed task (and any upstream/downstream task worth showing): task key, task type,
source path, and — for the failed one — the real code snippet around the failing line.

## Job orchestration
What triggers this job, and what this job triggers, one hop each direction. Say "none found" if
empty — don't omit the section.

## Data lineage
Tables read, tables written, and downstream consumers of those tables. Flag explicitly if this
section is incomplete due to a tool error rather than a genuine absence of lineage.

## Summary
2-4 sentences pulling the pieces into one narrative: what broke, what it depended on, what
depends on it. If the picture points at a specific root cause or a specific other job/table as
the real source of the problem, say so plainly. Suggest opsbuddy-fix as the next step only if a
code-level fix looks plausible from what you've found — otherwise just say what a human should
look at next.
```

Deliver the file to the user (don't just print it inline) — lineage reports get pasted into
tickets and re-read later, and a file survives that better than scrollback.
