# Working in this repo

Agent skills, one folder per skill under `skills/`, in the Agent Skills format. Read `CONTRIBUTING.md` before adding or changing a skill.

- Run `python3 scripts/validate.py` after any change to a skill or to `.claude-plugin/marketplace.json`, and fix everything it reports.
- Keep deterministic work in each skill's `scripts/`. Never put `$1`, `$2` … in a `SKILL.md`: Claude Code replaces them with skill arguments.
- Scripts use only `bash`, `curl` and the Python standard library, so a skill works on a fresh machine.
- In a shell script with `set -o pipefail`, don't pipe into a command that exits early (`awk '... exit'`, `head`): the writer gets SIGPIPE and `set -e` ends the script with no output. Use a here-string or a file.
- A change to a skill's behavior bumps `metadata.version` in its `SKILL.md`.
