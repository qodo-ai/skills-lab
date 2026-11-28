#!/usr/bin/env bash
# Run one skill for real in a fresh, non-interactive Claude Code session and
# save its answer plus a "SKILL FRICTION" report: every step that failed, was
# ambiguous, needed a permission, or was skipped.
#
# Usage: trial.sh <skill-folder> "<task for the skill>" [project-folder]
#
# The skill is copied into a throwaway plugin named "trial", so the user's
# installed copy is never touched. Only the skill call itself is pre-approved,
# so the run also tests whether the skill's own allowed-tools are enough to
# work without permission prompts. Prints the path of the report.
#
# Steps that run the project's own commands can't be pre-approved by a skill.
# To let a trial run them, list extra tools in TRIAL_ALLOWED_TOOLS, separated
# by newlines, e.g. TRIAL_ALLOWED_TOOLS='Bash(npm test)'.
set -euo pipefail

skill_dir="$(cd "${1:?skill folder}" && pwd)"
task="${2:?task for the skill}"
project="$(cd "${3:-.}" && pwd)"
name="$(basename "$skill_dir")"
command -v claude >/dev/null || { echo "ERROR: the claude CLI is not on PATH" >&2; exit 1; }

work="$(mktemp -d "${TMPDIR:-/tmp}/skill-trial.XXXXXX")"
mkdir -p "$work/trial/.claude-plugin" "$work/trial/skills"
printf '{"name": "trial", "description": "Throwaway plugin for one skill trial"}\n' > "$work/trial/.claude-plugin/plugin.json"
cp -R "$skill_dir" "$work/trial/skills/$name"
report="$work/report-$name.md"

prompt="$task Use the trial:$name skill to do it. Print your full answer. Then add a section titled 'SKILL FRICTION' that lists every step of the skill that failed, was ambiguous, needed a permission you didn't have, or that you skipped, and why. Change no files outside the conversation."

echo "Running trial of $name in $project (this can take several minutes)…" >&2
allowed=("Skill(trial:$name)" "Read")
while IFS= read -r tool; do [[ -n "$tool" ]] && allowed+=("$tool"); done <<<"${TRIAL_ALLOWED_TOOLS:-}"
(cd "$project" && claude -p --plugin-dir "$work/trial" --allowedTools "${allowed[@]}" -- "$prompt" \
  < /dev/null > "$report" 2>&1) || echo "WARN: the session exited with an error; see the report" >&2
echo "$report"
