# Expected Databricks MCP tool interface

This skill is read-only and MCP-only: it never falls back to REST calls, CLI commands, or
guessing. It expects a Databricks MCP server to already be connected in the session, exposing
the six tools below. The exact server name (the `mcp__<server>__` prefix) doesn't matter — the
skill looks for tools whose names and shapes match this contract. If the tools aren't found, stop
and tell the user their Databricks MCP server isn't connected (see SKILL.md, "Step 0").

Build the server against this contract (e.g. with the Databricks SDK for Python) and it will
work with this skill without any changes on the skill side.

## 1. `get_latest_failed_run`

Resolves a bare job ID down to the run that actually needs tracing. Only called when the user
gives a job ID without a specific run ID.

Input: `{ "job_id": string }`

Output:
```json
{ "run_id": string, "job_id": string, "state": "FAILED", "start_time": string }
```
If there's no failed run in recent history, return `{ "run_id": null }` rather than erroring —
the skill treats that as "nothing to trace" and says so.

## 2. `get_job_run`

The core telemetry call. One run of a job, broken down per task.

Input: `{ "run_id": string }`

Output:
```json
{
  "job_id": string,
  "job_name": string,
  "run_id": string,
  "run_page_url": string,
  "start_time": string,
  "end_time": string,
  "result_state": "FAILED" | "SUCCESS" | "TIMEDOUT" | "CANCELED" | string,
  "tasks": [
    {
      "task_key": string,
      "task_type": "notebook_task" | "spark_python_task" | "python_wheel_task" | "sql_task" | "dbt_task" | string,
      "source_path": string | null,
      "cluster_id": string | null,
      "state": string,
      "error_message": string | null,
      "stack_trace": string | null,
      "start_time": string,
      "end_time": string
    }
  ]
}
```
`source_path` is whatever identifies the code that actually ran: a notebook path, a `.py` file
path, or a wheel entry point. Return the full, untruncated `stack_trace` — the skill needs real
line numbers to correlate against source.

## 3. `get_job_config`

The job's static definition — this is what gives us the task dependency graph, independent of
any one run.

Input: `{ "job_id": string }`

Output:
```json
{
  "job_id": string,
  "job_name": string,
  "tasks": [
    { "task_key": string, "depends_on": [string], "task_type": string, "source_path": string | null }
  ],
  "schedule": { "quartz_cron_expression": string, "timezone_id": string } | null
}
```
`depends_on` is the list of task keys that must finish before this task runs — that's the DAG
edge list the skill uses to find the failed task's upstream and downstream neighbors.

## 4. `get_source_file`

Fetches the actual content of a notebook or python file, so the skill can quote the real code
around a stack trace line rather than describing it secondhand.

Input: `{ "path": string }` (the `source_path` from `get_job_run` / `get_job_config`)

Output:
```json
{ "path": string, "source_type": "workspace_notebook" | "repo_file" | "workspace_file", "content": string }
```
If the path can't be resolved (deleted notebook, detached repo, etc.), return
`{ "content": null }` — don't error. The skill reports the file as unavailable rather than
stopping the whole trace over one missing file.

## 5. `get_job_orchestration`

One hop of job-to-job orchestration in each direction — not the whole workspace's job graph,
just what's immediately adjacent to this job.

Input: `{ "job_id": string }`

Output:
```json
{
  "upstream_jobs": [ { "job_id": string, "job_name": string, "relation": string } ],
  "downstream_jobs": [ { "job_id": string, "job_name": string, "relation": string } ]
}
```
`relation` is a short human-readable label for *why* they're connected, e.g. `"triggers this job
via Run Job task"` or `"this job triggers it via Run Job task 'load_marts'"`. Empty arrays are a
valid, common answer — most jobs aren't orchestrated by other jobs.

## 6. `get_table_lineage`

Unity Catalog lineage for the tables this run actually touched — the "what did this feed, and
what fed it" answer at the data layer, plus who else depends on those tables.

Input: `{ "run_id": string }`

Output:
```json
{
  "tables_read": [string],
  "tables_written": [string],
  "downstream_consumers": [ { "type": "job" | "notebook" | "query" | "dashboard", "name": string, "id": string } ]
}
```
`downstream_consumers` is the blast-radius list: other things in the workspace that read the
tables this run wrote. If Unity Catalog lineage isn't enabled or the run touched no UC tables,
return empty arrays — that's a normal, reportable answer, not a failure.

---

## Error handling contract

Any of these tools can come back empty (no data) or fail (the call itself errors). Those are
different situations and the skill treats them differently — see SKILL.md's "Handling gaps"
section. In short: an empty result is a fact worth reporting ("no downstream jobs found"); a
tool-call error is a gap worth flagging ("couldn't reach table lineage for this run") — never
silently treated as "there is none."
