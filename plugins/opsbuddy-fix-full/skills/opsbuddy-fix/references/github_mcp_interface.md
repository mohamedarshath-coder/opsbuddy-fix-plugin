# Expected GitHub MCP tool interface

This plugin doesn't bundle a GitHub server — it expects one to already be connected, and works
with any of the options below. Match against the *shapes* (a file-read tool, branch/commit/push
tools, PR create/read/merge tools, a checks/status read tool), not exact tool names — those vary
by server and release.

## Option C — Claude's built-in GitHub connector (recommended)

Settings → Connectors → add **GitHub** → authorize against your account/org. No local process,
no PAT to manage by hand, works from any session including cloud ones. This is the default to
reach for — only fall back to A/B if it's genuinely unavailable in your environment.

## Option A — GitHub's official server (`github/github-mcp-server`)

Actively maintained, run via Docker or a built binary. Scope to at least
`repos, pull_requests, git, actions`. Key tools: `get_file_contents`, `create_branch`,
`create_or_update_file`, `push_files`, `create_pull_request`, `pull_request_read` (status/
files/diff via its parameter), `merge_pull_request`, `list_pull_requests`, `delete_branch` (name
varies by toolset), `get_job_logs` (lets a failed check be diagnosed, not just detected).

```json
{"mcpServers": {"github": {"command": "docker",
  "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "-e", "GITHUB_TOOLSETS",
           "ghcr.io/github/github-mcp-server"],
  "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "<your PAT>", "GITHUB_TOOLSETS": "repos,pull_requests,git,actions"}
}}}
```

## Option B — `@modelcontextprotocol/server-github` (deprecated but still runnable via npx)

No Docker needed. Fixed tool list: `get_file_contents`, `create_branch`,
`create_or_update_file`, `push_files`, `create_pull_request`, `get_pull_request_status`,
`merge_pull_request`, `list_commits`. **No Actions/log tools and no branch-deletion tool** — a
failing check can be detected but not diagnosed from log output on this option, and branch
cleanup needs the repo's own "Automatically delete head branches" setting (Settings → General →
Pull Requests) instead of a tool call.

```json
{"mcpServers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "<your PAT>"}
}}}
```
(Add `"NODE_EXTRA_CA_CERTS": "<path-to-corporate-root-CA>"` if you're behind a TLS-intercepting
proxy, e.g. Zscaler.)

## Any option

Getting a token (A/B only — C's connector auth replaces this): GitHub → Settings → Developer
settings → Personal access tokens, `repo` scope. Paste it into your local MCP config file
directly, never into a chat message — treat a token that touched a chat transcript as
compromised and regenerate it.

Before relying on any tool call, check the connected server's actual tool list and input schema
first — names and parameters drift between releases faster than this doc will stay current.
