"""Standalone Slack incident alert for the opsbuddy-fix plugin (Phase 10).

Self-contained on purpose -- no imports from any host project, so this plugin works regardless
of which repo it's installed alongside. Only dependency is `requests` (bundled in mcp-server's
.venv; this script is invoked with that same interpreter).

Usage:
  python slack_alert.py --jira-id OPS-1 --run-id 48213 --category "Schema Mismatch" \\
      --pr-url https://... --verdict PASS --status RESOLVED
  python slack_alert.py --text "arbitrary message"
"""

import argparse
import os
import sys

import requests


def send_message(webhook_url: str, text: str, blocks: list = None) -> None:
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    response = requests.post(webhook_url, json=payload, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Slack webhook returned {response.status_code}: {response.text}")


def build_incident_blocks(incident: dict) -> list:
    fields = [
        {"type": "mrkdwn", "text": f"*{key}*\n{value or '-'}"}
        for key, value in incident.items()
    ]
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "opsbuddy-fix incident summary"}},
        {"type": "section", "fields": fields},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default=None, help="Send an arbitrary message instead of an incident summary")
    parser.add_argument("--jira-id", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--pr-url", default="")
    parser.add_argument("--verdict", default="")
    parser.add_argument("--status", dest="execution_status", default="")
    args = parser.parse_args()

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("FATAL: SLACK_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1

    if args.text:
        send_message(webhook_url, text=args.text)
        print("[OK] Slack message sent")
        return 0

    incident = {
        "Jira Ticket": args.jira_id,
        "Databricks Run ID": args.run_id,
        "Error Category": args.category,
        "PR": args.pr_url,
        "Review Verdict": args.verdict,
        "Execution Status": args.execution_status,
    }
    text = f"[opsbuddy-fix] {args.jira_id or args.run_id} — {args.execution_status or 'update'}"
    send_message(webhook_url, text=text, blocks=build_incident_blocks(incident))
    print("[OK] Incident summary sent to Slack")
    return 0


if __name__ == "__main__":
    sys.exit(main())
