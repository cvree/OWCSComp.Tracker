#!/usr/bin/env bash
# =====================================================================
# sync-alert.sh — tell a person when a data workflow needs them ($0).
#
#   sync-alert.sh open  "<title>" <body-file>
#       Opens an issue with that title, or comments on the one already
#       open (one issue per problem, however long it lasts). GitHub emails
#       the repository owner for both. Also posts to Discord when a
#       webhook secret is configured (pipeline/discord_notify.py).
#   sync-alert.sh close "<title>" "<comment>"
#       Closes that issue, if one is open, once the workflow is healthy.
#
# Needs GH_TOKEN and `issues: write`. Never fails the calling job: an
# alert that cannot be delivered must not turn a green import red.
# =====================================================================
set -uo pipefail
mode="${1:?open|close}"; title="${2:?title}"; arg="${3:-}"
run_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"

number=$(gh issue list --state open --limit 200 --json number,title \
          --jq "map(select(.title == \"$title\")) | .[0].number // empty" 2>/dev/null || true)

if [ "$mode" = "close" ]; then
  if [ -n "$number" ]; then
    gh issue close "$number" --comment "${arg:-Healthy again.} ([run]($run_url))" || true
  fi
  exit 0
fi

body=$(mktemp)
{ cat "$arg" 2>/dev/null || echo "(no details were written)"; echo; echo "Run: $run_url"; } | head -c 60000 > "$body"
if [ -n "$number" ]; then
  gh issue comment "$number" --body-file "$body" || echo "::warning::could not comment on issue #$number"
else
  gh issue create --title "$title" --body-file "$body" || echo "::warning::could not open an alert issue"
fi
python pipeline/discord_notify.py alert --title "$title" --url "$run_url" \
  --body "$(head -c 1500 "$body")" || true
exit 0
