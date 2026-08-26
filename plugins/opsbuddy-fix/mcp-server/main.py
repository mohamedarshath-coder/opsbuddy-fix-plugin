"""Entry point for `uv run main.py` (what .mcp.json actually invokes).

All tool definitions and startup/auth logic live in server.py; this just starts the stdio
server after that module-level setup (host/token validation, WorkspaceClient auth check) runs.
"""

from server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
