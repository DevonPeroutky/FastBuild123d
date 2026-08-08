#!/usr/bin/env bash
# Injects PYTHON_CODE_STANDARDS.md into the model's context the first time an
# agent touches a .py file. Fires once per agent (main session and each
# subagent get their own injection, keyed on transcript_path) so planning,
# implementation, and review contexts all see the standards without paying the
# context cost more than once.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
standards="$repo_root/PYTHON_CODE_STANDARDS.md"
[ -f "$standards" ] || exit 0

payload="$(cat)"

file_path="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')"
case "$file_path" in
  *.py) ;;
  *) exit 0 ;;
esac

agent_key="$(printf '%s' "$payload" | jq -r '.transcript_path // .session_id // "unknown"')"
marker_dir="${TMPDIR:-/tmp}/claude-python-standards"
mkdir -p "$marker_dir" || exit 0
marker="$marker_dir/$(printf '%s' "$agent_key" | shasum | cut -d' ' -f1)"
[ -e "$marker" ] && exit 0
: > "$marker"

find "$marker_dir" -type f -mtime +7 -delete 2>/dev/null

jq -n --rawfile body "$standards" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: (
      "Repo standards for PYTHON_CODE_STANDARDS.md are reproduced below. They are binding for all Python work in this repository: designing plans, writing or modifying code, and reviewing code. Apply them for the rest of this session, and run the decision checklist before presenting a plan, finishing an edit, or reporting a review.\n\n"
      + $body
    )
  },
  suppressOutput: true
}'
