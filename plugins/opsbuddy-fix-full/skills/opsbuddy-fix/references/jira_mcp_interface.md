# Expected Jira MCP tool interface

This plugin doesn't bundle a Jira server — it expects one to already be connected. Match against
the *shapes* below, not exact tool names.

## Recommended — Claude's built-in Atlassian connector

Settings → Connectors → add **Atlassian** → authorize against your Jira site. Tools typically
appear prefixed `mcp__<something>_Atlassian__...` (exact prefix varies by session/host). Look
for tools shaped like:

- **Create an issue** — needs a `cloudId` (or equivalent site identifier — a lookup tool such as
  `getAccessibleAtlassianResources` usually resolves this from your connected site),
  `projectKey`, `issueTypeName`, `summary`, `description`.
- **Add a comment** — `issueIdOrKey`, `body`.
- **Search issues** (for the dedup check in Phase 3) — a JQL search tool, e.g.
  `project = OPS AND summary ~ "run <run_id>"`.
- **Transition an issue** — moves a ticket to a new status (e.g. "In Review").
- **Get project issue types** — check what issue types actually exist in the target project
  before assuming `Incident` exists; fall back to the next best type (`Bug` > `Task` > `Story`)
  if not.

## If no Jira MCP is connected

Stop and say so plainly at Phase 0 / Phase 3 rather than fabricating a ticket ID or silently
skipping the ticket step. State clearly in the final report that Jira steps were skipped and why.

## Any option

Check the connected server's actual tool list and input schema before calling anything — exact
names vary between Atlassian connector versions.
