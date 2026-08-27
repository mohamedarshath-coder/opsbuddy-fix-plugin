---
name: testing
description: >-
  Runs static syntax and logic verification on a set of changed files, with one bounded retry on
  failure before escalating. Generic reusable sub-skill — invoked by opsbuddy-fix Phase 5 for its
  remediation static-validation step, and usable standalone after any code change ("run static
  checks on these files", "verify this diff compiles and lints clean"). Reports a plain
  PASS/FAIL verdict plus full output, and states plainly which validation mode it actually ran.
---

# testing

**Argument**: a list of changed file paths and their new content (or a short description of what
changed, if this is standalone and paths weren't tracked).

Two possible modes — pick based on what's actually available, and **say which one you used** in
the report. Don't silently claim a reasoning pass caught everything a real linter/test run
would.

## Mode 1 — Real execution (when a local checkout + shell access exist)

If you have Bash tool access and the changed files exist in a real local checkout (not just as
in-memory content from a GitHub MCP fetch), run the real tools:

```bash
black --check <files>
isort --check <files>
flake8 --max-line-length=120 <files>
python -m py_compile <files>
```
Then, if a matching test file exists nearby (e.g. `test_<module>.py`):
```bash
pytest <path-to-test-file> -v
```
For dbt/SQL: `dbt compile --select <model_name>` and `sqlfluff lint <file>` if the repo has dbt
configured.

## Mode 2 — Reasoning pass (the common case for this plugin — no local checkout)

This plugin applies fixes via GitHub MCP content APIs (fetch → edit text → push), which never
creates a local checkout to run tools against. When that's the situation, do a careful manual
read of the new file content instead:

- Unbalanced brackets/quotes/parens.
- Indentation errors (fatal in Python).
- A name used before it's defined, or an import that's missing for something newly referenced.
- Whether the fix could plausibly still raise the *same* error class under a slightly different
  input (e.g. did a null-guard actually cover every path that reaches the failing line?).

This is a reasoning pass, not a linter or test run — it catches obvious mistakes, not everything
real tooling would. Say so plainly in the report.

## On Failure (either mode)

1. Show the failing tool's output (or the specific issue found in Mode 2) in full.
2. Propose a specific, targeted fix — not a broad rewrite.
3. Apply it and re-check **once**.
4. Still failing after that single retry — stop and escalate back to the caller with the
   failure detail rather than looping indefinitely.

## Report

Return to the caller: which mode ran and why, files checked, tools/checks run and their
pass/fail, any retry and its outcome, and a final `PASS` / `FAIL` verdict.
