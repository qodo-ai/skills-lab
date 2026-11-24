#!/usr/bin/env bash
# Says whether qodo-ai/skills-lab on GitHub has a newer version of this skill.
# It compares git blob hashes of the installed files with the hashes GitHub
# reports, and downloads no code. Always prints one line and exits 0, so a
# failed check never blocks the skill.
#
# Plugin installs update through the plugin marketplace. This check matters
# most for copies installed with `npx skills add` or by hand.
set -uo pipefail

repo="qodo-ai/skills-lab"
skill_dir="$(cd "$(dirname "$0")/.." && pwd)"
skill="$(basename "$skill_dir")"

skipped() { echo "Update check skipped: $1. Continue with the installed version."; exit 0; }

command -v git >/dev/null || skipped "git is not installed"
command -v python3 >/dev/null || skipped "python3 is not installed"
tree="$(curl -fsSL --max-time 10 \
  "https://api.github.com/repos/$repo/git/trees/main:skills/$skill?recursive=1" 2>/dev/null)" \
  || skipped "GitHub did not answer (offline, rate limited, or $skill is not published yet)"

remote="$(python3 -c '
import json, sys
for e in json.load(sys.stdin).get("tree", []):
    if e.get("type") == "blob":
        print(e["sha"] + "\t" + e["path"])
' <<<"$tree" 2>/dev/null)" || skipped "GitHub's answer could not be read"
[[ -z "$remote" ]] && skipped "GitHub listed no files for $skill"

while IFS=$'\t' read -r sha path; do
  local_sha="$(git hash-object --no-filters "$skill_dir/$path" 2>/dev/null || true)"
  if [[ "$local_sha" != "$sha" ]]; then
    echo "$skill has an update on GitHub. Tell the user: npx skills update $skill -g, or /plugin to update the plugin."
    exit 0
  fi
done <<<"$remote"
echo "$skill is up to date."
