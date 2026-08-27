"""
Opsbuddy Databricks MCP Server
===============================

Exposes 6 read-only tools over Databricks Jobs, Workspace, and Unity Catalog lineage data, a
repo-resolution tool the opsbuddy-fix skill uses to find where to open a PR, and three
write-capable tools for opsbuddy-fix-style real-verification and incident logging:

    get_latest_failed_run(job_id)
    get_job_run(run_id)
    get_job_config(job_id)
    get_source_file(path)
    get_job_orchestration(job_id)
    get_table_lineage(run_id)
    get_repo_mapping(source_path, job_id)  -- resolves a workspace path to its backing GitHub repo
    sync_repo(repo_url, branch)      -- WRITES to Databricks; points a Databricks Repo at a branch
    trigger_job_run(job_id, force)   -- WRITES to Databricks; gated, see its docstring
    log_incident(record)             -- WRITES to Databricks; inserts one incident-log row

Auth: a Databricks personal access token (PAT) + workspace host, read from environment
variables (see .env.example). Every tool except sync_repo/trigger_job_run is a pure read;
sync_repo re-points a workspace Repo checkout (needed because Databricks Repos does NOT
auto-sync on a GitHub push), and trigger_job_run re-runs a real job and is refused by default
unless the job is explicitly allowlisted or a human passes force=true.

Run it:
    pip install -r requirements.txt
    export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
    export DATABRICKS_TOKEN=dapi...
    python server.py

Then point an MCP client (Claude Desktop, Claude Code, etc.) at it as a stdio server --
see README.md for the exact client config.
"""

import base64
import os
import sys
import time
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs as dbx_jobs
from databricks.sdk.service import workspace as dbx_workspace
from databricks.sdk.errors import DatabricksError

from mcp.server import MCPServer

# ---------------------------------------------------------------------------
# Config & auth
# ---------------------------------------------------------------------------

DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "").strip()
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "").strip()
# Optional: only needed for get_table_lineage, which queries a Unity Catalog system table
# via a SQL warehouse. Leave unset and that one tool will report itself unavailable.
SQL_WAREHOUSE_ID = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "").strip() or None
# get_job_orchestration's "upstream" direction has to scan other jobs looking for ones that
# point back at this job -- there's no reverse index for it in the Jobs API. Cap the scan so
# a workspace with thousands of jobs doesn't turn one lineage trace into a multi-minute call.
ORCHESTRATION_SCAN_LIMIT = int(os.environ.get("DATABRICKS_ORCHESTRATION_SCAN_LIMIT", "200"))

# trigger_job_run is the one tool that writes -- it re-runs a real job, which can execute code
# that writes real data. Only job IDs listed here (or "all", which should only ever be set in a
# non-production workspace) may be auto-triggered; anything else needs force=true, which should
# only be passed after a human has explicitly approved re-running that specific job.
TRIGGER_ALLOWLIST = os.environ.get("DATABRICKS_TRIGGER_ALLOWLIST", "").strip()
TRIGGER_TIMEOUT_SECONDS = int(os.environ.get("DATABRICKS_TRIGGER_TIMEOUT_SECONDS", "600"))
TRIGGER_POLL_INTERVAL_SECONDS = int(os.environ.get("DATABRICKS_TRIGGER_POLL_INTERVAL_SECONDS", "10"))
TERMINAL_LIFE_CYCLE_STATES = ("TERMINATED", "SKIPPED", "INTERNAL_ERROR")

# Three-part Unity Catalog table log_incident writes rows into. Needs DATABRICKS_SQL_WAREHOUSE_ID
# set too (same requirement as get_table_lineage) since writing also goes through a SQL warehouse.
OPS_INCIDENT_TABLE = os.environ.get("DATABRICKS_OPS_INCIDENT_TABLE", "").strip()


def _is_trigger_allowed(job_id: str) -> bool:
    if TRIGGER_ALLOWLIST.lower() == "all":
        return True
    allowed_ids = {x.strip() for x in TRIGGER_ALLOWLIST.split(",") if x.strip()}
    return str(job_id) in allowed_ids

if not DATABRICKS_HOST or not DATABRICKS_TOKEN:
    print(
        "FATAL: DATABRICKS_HOST and DATABRICKS_TOKEN must both be set.\n"
        "  export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com\n"
        "  export DATABRICKS_TOKEN=dapi...your-pat-token...\n"
        "See .env.example.",
        file=sys.stderr,
    )
    sys.exit(1)

client = WorkspaceClient(host=DATABRICKS_HOST, token=DATABRICKS_TOKEN)

# Fail fast on bad host/token rather than surfacing a confusing error on the first real tool
# call -- auth problems are much easier to diagnose here than three tool-calls deep.
try:
    _me = client.current_user.me()
    print(f"Connected to {DATABRICKS_HOST} as {_me.user_name}", file=sys.stderr)
except Exception as exc:  # noqa: BLE001 - this is a startup diagnostic, not a tool call
    print(
        f"FATAL: could not authenticate to {DATABRICKS_HOST} with the given token.\n"
        f"  {exc}",
        file=sys.stderr,
    )
    sys.exit(1)

mcp = MCPServer("opsbuddy-databricks")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _iso(ms):
    """Databricks timestamps are epoch milliseconds; MCP contract wants ISO 8601 strings."""
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _task_type_and_source(task):
    """
    A task's "type" and "source" live in different sub-objects depending on which kind of
    task it is. Only one of these should be set on any given task.
    """
    if getattr(task, "notebook_task", None):
        return "notebook_task", task.notebook_task.notebook_path
    if getattr(task, "spark_python_task", None):
        return "spark_python_task", task.spark_python_task.python_file
    if getattr(task, "python_wheel_task", None):
        pw = task.python_wheel_task
        label = ":".join(filter(None, [getattr(pw, "package_name", None), getattr(pw, "entry_point", None)]))
        return "python_wheel_task", label or None
    if getattr(task, "sql_task", None):
        st = task.sql_task
        if getattr(st, "file", None):
            return "sql_task", st.file.path
        return "sql_task", None
    if getattr(task, "dbt_task", None):
        return "dbt_task", task.dbt_task.project_directory
    if getattr(task, "spark_jar_task", None):
        return "spark_jar_task", task.spark_jar_task.main_class_name
    if getattr(task, "pipeline_task", None):
        return "pipeline_task", task.pipeline_task.pipeline_id
    if getattr(task, "run_job_task", None):
        return "run_job_task", None
    return "unknown", None


def _job_name(job_id):
    """Best-effort job name lookup -- used when annotating orchestration edges."""
    try:
        j = client.jobs.get(job_id=int(job_id))
        return j.settings.name if j.settings else f"job {job_id}"
    except Exception:  # noqa: BLE001
        return f"job {job_id}"


# ---------------------------------------------------------------------------
# 1. get_latest_failed_run
# ---------------------------------------------------------------------------


@mcp.tool()
def get_latest_failed_run(job_id: str) -> dict:
    """Find the most recent FAILED run of a job. Returns run_id=None if none is found."""
    try:
        runs = client.jobs.list_runs(job_id=int(job_id), active_only=False, limit=25)
        for run in runs:
            state = getattr(run, "state", None)
            if state and state.result_state == dbx_jobs.RunResultState.FAILED:
                return {
                    "run_id": str(run.run_id),
                    "job_id": str(job_id),
                    "state": "FAILED",
                    "start_time": _iso(run.start_time),
                }
        return {"run_id": None}
    except DatabricksError as exc:
        return {"run_id": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# 2. get_job_run
# ---------------------------------------------------------------------------


@mcp.tool()
def get_job_run(run_id: str) -> dict:
    """Full telemetry for one job run, broken down per task, including error/stack trace."""
    try:
        run = client.jobs.get_run(run_id=int(run_id))
    except DatabricksError as exc:
        return {"error": str(exc)}

    tasks = []
    for t in run.tasks or []:
        task_type, source_path = _task_type_and_source(t)
        state = getattr(t, "state", None)
        result_state = state.result_state.value if state and state.result_state else None
        error_message = None
        stack_trace = None
        if result_state == "FAILED" and getattr(t, "run_id", None):
            try:
                output = client.jobs.get_run_output(run_id=t.run_id)
                error_message = output.error
                stack_trace = output.error_trace
            except DatabricksError as exc:
                error_message = f"<could not fetch task output: {exc}>"
        cluster_id = getattr(t, "existing_cluster_id", None)
        if not cluster_id and getattr(t, "cluster_instance", None):
            cluster_id = t.cluster_instance.cluster_id
        tasks.append(
            {
                "task_key": t.task_key,
                "task_type": task_type,
                "source_path": source_path,
                "cluster_id": cluster_id,
                "state": result_state,
                "error_message": error_message,
                "stack_trace": stack_trace,
                "start_time": _iso(getattr(t, "start_time", None)),
                "end_time": _iso(getattr(t, "end_time", None)),
            }
        )

    return {
        "job_id": str(run.job_id) if run.job_id else None,
        "job_name": getattr(run, "run_name", None),
        "run_id": str(run.run_id),
        "run_page_url": run.run_page_url,
        "start_time": _iso(run.start_time),
        "end_time": _iso(run.end_time),
        "result_state": run.state.result_state.value if run.state and run.state.result_state else None,
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# 3. get_job_config
# ---------------------------------------------------------------------------


@mcp.tool()
def get_job_config(job_id: str) -> dict:
    """The job's static definition: task dependency graph (depends_on) and schedule."""
    try:
        job = client.jobs.get(job_id=int(job_id))
    except DatabricksError as exc:
        return {"error": str(exc)}

    settings = job.settings
    tasks = []
    for t in (settings.tasks or []) if settings else []:
        task_type, source_path = _task_type_and_source(t)
        depends_on = [d.task_key for d in (t.depends_on or [])]
        tasks.append(
            {
                "task_key": t.task_key,
                "depends_on": depends_on,
                "task_type": task_type,
                "source_path": source_path,
            }
        )

    schedule = None
    if settings and settings.schedule:
        schedule = {
            "quartz_cron_expression": settings.schedule.quartz_cron_expression,
            "timezone_id": settings.schedule.timezone_id,
        }

    return {
        "job_id": str(job_id),
        "job_name": settings.name if settings else None,
        "tasks": tasks,
        "schedule": schedule,
    }


# ---------------------------------------------------------------------------
# 4. get_source_file
# ---------------------------------------------------------------------------


@mcp.tool()
def get_source_file(path: str) -> dict:
    """Fetch the actual source of a notebook or workspace file so real code can be quoted."""
    if not path:
        return {"path": path, "source_type": "unknown", "content": None, "error": "empty path"}

    source_type = "repo_file" if path.startswith("/Repos/") else "workspace_notebook"
    try:
        status = client.workspace.get_status(path)
        if status.object_type == dbx_workspace.ObjectType.FILE:
            source_type = "repo_file" if path.startswith("/Repos/") else "workspace_file"
    except DatabricksError:
        pass  # not fatal -- export below will surface the real problem if the path is bad

    try:
        exported = client.workspace.export(path=path, format=dbx_workspace.ExportFormat.SOURCE)
        raw = base64.b64decode(exported.content) if exported.content else b""
        return {"path": path, "source_type": source_type, "content": raw.decode("utf-8", errors="replace")}
    except DatabricksError as exc:
        return {"path": path, "source_type": source_type, "content": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# 5. get_job_orchestration
# ---------------------------------------------------------------------------


@mcp.tool()
def get_job_orchestration(job_id: str) -> dict:
    """
    One hop of job-to-job orchestration each direction.

    Downstream (this job -> others) is cheap: it's just this job's own Run Job tasks.
    Upstream (others -> this job) is expensive: the Jobs API has no reverse index, so this
    scans other jobs' configs looking for a Run Job task that targets this job_id, capped at
    DATABRICKS_ORCHESTRATION_SCAN_LIMIT jobs. In a large workspace this may not see every
    upstream trigger -- `scan_limit_hit: true` in the response means it stopped early.
    """
    downstream = []
    try:
        job = client.jobs.get(job_id=int(job_id))
        for t in (job.settings.tasks or []) if job.settings else []:
            if getattr(t, "run_job_task", None):
                target_id = t.run_job_task.job_id
                downstream.append(
                    {
                        "job_id": str(target_id),
                        "job_name": _job_name(target_id),
                        "relation": f"this job triggers it via Run Job task '{t.task_key}'",
                    }
                )
    except DatabricksError as exc:
        return {"upstream_jobs": [], "downstream_jobs": [], "error": str(exc)}

    upstream = []
    scanned = 0
    scan_limit_hit = False
    try:
        # expand_tasks=True gets each job's task list in the same paginated call, instead of
        # one extra get() per job -- much cheaper than the N+1 version for a large workspace.
        for candidate in client.jobs.list(expand_tasks=True, limit=min(ORCHESTRATION_SCAN_LIMIT, 100)):
            if str(candidate.job_id) == str(job_id):
                continue
            if scanned >= ORCHESTRATION_SCAN_LIMIT:
                scan_limit_hit = True
                break
            scanned += 1
            for t in (candidate.settings.tasks or []) if candidate.settings else []:
                if getattr(t, "run_job_task", None) and str(t.run_job_task.job_id) == str(job_id):
                    upstream.append(
                        {
                            "job_id": str(candidate.job_id),
                            "job_name": candidate.settings.name if candidate.settings else str(candidate.job_id),
                            "relation": f"triggers this job via Run Job task '{t.task_key}'",
                        }
                    )
    except DatabricksError:
        pass  # partial upstream results are still useful; don't blow away what we found

    return {
        "upstream_jobs": upstream,
        "downstream_jobs": downstream,
        "scan_limit_hit": scan_limit_hit,
    }


# ---------------------------------------------------------------------------
# 6. get_table_lineage
# ---------------------------------------------------------------------------


@mcp.tool()
def get_table_lineage(run_id: str) -> dict:
    """
    Unity Catalog table lineage for a specific job run, via the system.access.table_lineage
    system table (requires Unity Catalog lineage tracking enabled on the workspace, and a
    running/serverless SQL warehouse configured via DATABRICKS_SQL_WAREHOUSE_ID).

    NOTE: the exact column names of system.access.table_lineage can vary by workspace/version.
    If this starts returning errors, run `DESCRIBE system.access.table_lineage` in a SQL editor
    and adjust the column names in the queries below to match.
    """
    if not SQL_WAREHOUSE_ID:
        return {
            "tables_read": [],
            "tables_written": [],
            "downstream_consumers": [],
            "error": "DATABRICKS_SQL_WAREHOUSE_ID is not configured -- table lineage requires a SQL warehouse to query Unity Catalog system tables.",
        }

    def run_query(statement):
        resp = client.statement_execution.execute_statement(
            warehouse_id=SQL_WAREHOUSE_ID,
            statement=statement,
            wait_timeout="30s",
        )
        if not resp.result or not resp.result.data_array:
            return []
        return resp.result.data_array

    try:
        rows = run_query(
            f"""
            SELECT DISTINCT source_table_full_name, target_table_full_name
            FROM system.access.table_lineage
            WHERE entity_type = 'JOB' AND entity_run_id = '{run_id}'
            """
        )
    except DatabricksError as exc:
        return {
            "tables_read": [],
            "tables_written": [],
            "downstream_consumers": [],
            "error": f"table_lineage query failed: {exc}",
        }

    tables_read = sorted({r[0] for r in rows if r and r[0]})
    tables_written = sorted({r[1] for r in rows if r and r[1]})

    downstream_consumers = []
    if tables_written:
        in_clause = ", ".join(f"'{t}'" for t in tables_written)
        try:
            consumer_rows = run_query(
                f"""
                SELECT DISTINCT entity_type, entity_id
                FROM system.access.table_lineage
                WHERE source_table_full_name IN ({in_clause})
                  AND entity_run_id != '{run_id}'
                """
            )
            for entity_type, entity_id in consumer_rows:
                name = _job_name(entity_id) if entity_type == "JOB" else str(entity_id)
                downstream_consumers.append(
                    {"type": (entity_type or "unknown").lower(), "name": name, "id": str(entity_id)}
                )
        except DatabricksError as exc:
            downstream_consumers = []
            return {
                "tables_read": list(tables_read),
                "tables_written": list(tables_written),
                "downstream_consumers": downstream_consumers,
                "error": f"downstream consumer lookup failed (tables read/written above are still valid): {exc}",
            }

    return {
        "tables_read": list(tables_read),
        "tables_written": list(tables_written),
        "downstream_consumers": downstream_consumers,
    }


# ---------------------------------------------------------------------------
# 7. sync_repo (WRITES to Databricks -- points a Repo checkout at a branch)
# ---------------------------------------------------------------------------


@mcp.tool()
def sync_repo(repo_url: str, branch: str) -> dict:
    """Point the workspace's Databricks Repo for `repo_url` at `branch`'s latest commit.

    Databricks Repos does NOT auto-sync when you push to GitHub -- a job whose notebook path
    lives under /Repos/... will keep running whatever commit it was last synced to until this
    is called. Call this before trigger_job_run when verifying a fix on a hotfix branch, and
    again to point back at the base branch once verification is done.

    Assumes the Repo folder name matches the last path segment of repo_url (e.g.
    ".../org/my-repo.git" -> Repo folder "my-repo") under the current user's own /Repos/<user>/
    path. If the Repo was cloned under a different folder name or a shared/service-principal
    path, this will fail with a clear error rather than silently syncing the wrong thing.
    """
    try:
        me = client.current_user.me()
    except DatabricksError as exc:
        return {"error": f"could not resolve current user: {exc}"}

    repo_name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[: -len(".git")]
    path = f"/Repos/{me.user_name}/{repo_name}"

    try:
        status = client.workspace.get_status(path)
    except DatabricksError as exc:
        return {
            "error": (
                f"no Databricks Repo found at {path}: {exc}. If it was cloned under a "
                "different folder name, this tool can't find it automatically."
            )
        }

    try:
        client.repos.update(repo_id=status.object_id, branch=branch)
        updated = client.repos.get(repo_id=status.object_id)
    except DatabricksError as exc:
        return {"error": f"could not sync {path} to {branch}: {exc}"}

    return {"path": path, "branch": branch, "head_commit_id": updated.head_commit_id}


# ---------------------------------------------------------------------------
# 8. trigger_job_run (WRITES to Databricks -- re-runs a job)
# ---------------------------------------------------------------------------


@mcp.tool()
def trigger_job_run(job_id: str, force: str = "false") -> dict:
    """Re-run a job and block until it finishes -- proves a fix actually works, instead of
    just trusting that a PR's diff looks right.

    This is the one tool in this server that writes to Databricks: re-running a job can
    execute code that writes real data, possibly from an unmerged, not-yet-human-reviewed
    branch. It is refused by default. To allow it:

      - Set DATABRICKS_TRIGGER_ALLOWLIST to a comma-separated list of job IDs that are safe
        to auto-re-run (e.g. sandbox/staging jobs), or to "all" -- only ever do that in a
        non-production workspace.
      - Or pass force="true", which should only happen after a human has explicitly
        approved re-running this specific job. Do not set force="true" on your own
        initiative just because the allowlist check failed -- ask first.

    Returns run_id, life_cycle_state, result_state, run_page_url, and succeeded (bool) on
    completion, or an "error" key if refused or if the run didn't finish within
    DATABRICKS_TRIGGER_TIMEOUT_SECONDS (default 600s).
    """
    allowed = _is_trigger_allowed(job_id)
    if not allowed and force.strip().lower() != "true":
        return {
            "error": (
                f"job_id {job_id} is not in DATABRICKS_TRIGGER_ALLOWLIST. Re-running a "
                "real job against unreviewed code needs explicit human approval first -- "
                "ask the user, then call this again with force=\"true\" if they approve."
            ),
            "needs_approval": True,
        }

    try:
        run = client.jobs.run_now(job_id=int(job_id))
    except DatabricksError as exc:
        return {"error": f"could not trigger job {job_id}: {exc}"}

    run_id = run.run_id
    elapsed = 0
    while elapsed < TRIGGER_TIMEOUT_SECONDS:
        try:
            run = client.jobs.get_run(run_id=run_id)
        except DatabricksError as exc:
            return {"run_id": str(run_id), "error": f"could not poll run {run_id}: {exc}"}

        state = run.state
        life_cycle = state.life_cycle_state.value if state and state.life_cycle_state else "UNKNOWN"
        result_state = state.result_state.value if state and state.result_state else None
        if life_cycle in TERMINAL_LIFE_CYCLE_STATES:
            return {
                "run_id": str(run_id),
                "life_cycle_state": life_cycle,
                "result_state": result_state,
                "run_page_url": run.run_page_url,
                "succeeded": result_state == "SUCCESS",
            }
        time.sleep(TRIGGER_POLL_INTERVAL_SECONDS)
        elapsed += TRIGGER_POLL_INTERVAL_SECONDS

    return {
        "run_id": str(run_id),
        "error": f"run {run_id} did not finish within {TRIGGER_TIMEOUT_SECONDS}s",
    }


# ---------------------------------------------------------------------------
# 9. get_repo_mapping (resolves a workspace source_path to the GitHub repo backing it)
# ---------------------------------------------------------------------------


@mcp.tool()
def get_repo_mapping(source_path: str, job_id: str = "") -> dict:
    """Resolve a workspace source_path to the GitHub repo it's actually backed by, so a fix can
    be committed against the real repo instead of guessed at from the job name.

    Two mechanisms, tried in order:
    1. source_path under /Repos/... -- matched against a live Databricks Repo checkout
       (client.repos.list()) to get its git remote URL and branch.
    2. Otherwise, if job_id is given -- checked against that job's job-level git_source
       (jobs/get), used when a job runs straight from a Git repo without a Repos checkout.

    Always pass job_id when you have it -- mechanism 2 needs it and mechanism 1 doesn't use it,
    so passing it costs nothing. Returns {repo_url, branch, relative_path_in_repo, error: null}
    on success, or {error: "..."} if source_path isn't backed by git at all (a genuine
    workspace-native notebook, or job_id was needed but omitted).

    NOTE: field names on the job-level git_source object can vary slightly across
    databricks-sdk versions -- if this errors on a job you know has Git source configured,
    check `job.settings.git_source`'s actual attributes in your SDK version and adjust.
    """
    if source_path.startswith("/Repos/"):
        try:
            for repo in client.repos.list():
                if repo.path and source_path.startswith(repo.path + "/"):
                    return {
                        "repo_url": repo.url,
                        "branch": repo.branch,
                        "relative_path_in_repo": source_path[len(repo.path) + 1 :],
                        "error": None,
                    }
        except DatabricksError as exc:
            return {"error": f"could not list Databricks Repos: {exc}"}
        return {
            "error": f"{source_path} is under /Repos/ but no matching Repo checkout was found."
        }

    if job_id:
        try:
            job = client.jobs.get(job_id=int(job_id))
        except DatabricksError as exc:
            return {"error": f"could not fetch job {job_id}: {exc}"}
        git_source = getattr(job.settings, "git_source", None) if job.settings else None
        git_url = getattr(git_source, "git_url", None) if git_source else None
        if git_url:
            branch = getattr(git_source, "git_branch", None) or getattr(
                git_source, "git_tag", None
            )
            return {
                "repo_url": git_url,
                "branch": branch,
                "relative_path_in_repo": source_path,
                "error": None,
            }

    return {
        "error": (
            f"{source_path} is not under /Repos/ and job {job_id or '(none given)'} has no "
            "git_source configured -- this is a workspace-native file/notebook with no git "
            "repo backing it. There is no repo to open a PR against."
        )
    }


# ---------------------------------------------------------------------------
# 10. log_incident (WRITES to Databricks -- inserts a row into the ops incident log table)
# ---------------------------------------------------------------------------


def _sql_literal(value):
    """Render a Python value as a SQL literal for the INSERT below."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


@mcp.tool()
def log_incident(record: dict) -> dict:
    """Insert one row into the opsbuddy-fix incident log table (Phase 10 of the opsbuddy-fix
    skill). `record` is a flat dict of column_name -> value; include only columns that actually
    exist in DATABRICKS_OPS_INCIDENT_TABLE. Requires DATABRICKS_OPS_INCIDENT_TABLE and
    DATABRICKS_SQL_WAREHOUSE_ID both set -- writes go through the same SQL warehouse
    get_table_lineage reads through.
    """
    if not OPS_INCIDENT_TABLE:
        return {"error": "DATABRICKS_OPS_INCIDENT_TABLE is not configured."}
    if not SQL_WAREHOUSE_ID:
        return {"error": "DATABRICKS_SQL_WAREHOUSE_ID is not configured -- writes need a SQL warehouse."}

    columns = list(record.keys())
    values = [_sql_literal(record[c]) for c in columns]
    statement = (
        f"INSERT INTO {OPS_INCIDENT_TABLE} ({', '.join(columns)}, loaded_at) "
        f"VALUES ({', '.join(values)}, current_timestamp())"
    )
    try:
        client.statement_execution.execute_statement(
            warehouse_id=SQL_WAREHOUSE_ID, statement=statement, wait_timeout="30s"
        )
    except DatabricksError as exc:
        return {"error": f"log_incident insert failed: {exc}"}

    return {"table": OPS_INCIDENT_TABLE, "inserted": True}


if __name__ == "__main__":
    mcp.run(transport="stdio")
