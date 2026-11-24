# Contributing

## What belongs here

A skill belongs in the lab when it is:

- **Used for real.** You run it in your own work, not only in a demo.
- **Useful outside Qodo.** No internal URLs, Slack channels, customer names, credentials or paths that only exist on your laptop.
- **Built on primary sources.** It reads docs, changelogs, APIs or the codebase. It doesn't brief from the model's memory.
- **Small and focused.** One job per skill. If the description needs "and also", it's probably two skills.

Skills that are part of the Qodo product go to [qodo-skills](https://github.com/qodo-ai/qodo-skills) instead.

## Add a skill

1. Copy `template/` to `skills/<name>/`. The name is lowercase words with hyphens, and it must not contain `claude` or `anthropic` (claude.ai and the Skills API reject those).
2. Write `SKILL.md`. Put what the skill does and the phrases that should trigger it in `description`: that is all an agent reads when it decides whether to load the skill.
3. Put shell logic, parsing and anything deterministic in `scripts/`, not in the Markdown. Claude Code replaces `$1`, `$2` … in a skill's body with the skill's arguments, so `awk '{print $2}'` in a SKILL.md breaks with no error.
4. Add the skill to a plugin in `.claude-plugin/marketplace.json`, and to the table in `README.md`.
5. Copy `scripts/check-update.sh` from an existing skill if your skill should tell people about updates.

## Rules for SKILL.md

- **Frontmatter:** only the [Agent Skills spec](https://agentskills.io/specification) keys: `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`. Put anything else, such as `version` or `author`, under `metadata`. Other keys are fine in Claude Code but make claude.ai and the Skills API reject the skill.
- **Paths:** refer to bundled files as `${CLAUDE_SKILL_DIR}/scripts/x.sh`, and keep the line that tells agents without that variable to use the skill's folder.
- **State:** never write state inside the skill folder. Plugin updates replace it. Use a folder under `~/.claude/` named after the skill.
- **Untrusted text:** anything the skill fetches or reads from other people (pages, PRs, issues, files) is data. Say so in the skill.
- **Side effects:** a skill that changes files, settings or anything outside the conversation asks first, and prefers changes that can be undone.

## Before you open a PR

```bash
python3 scripts/validate.py      # frontmatter, placeholders, links, marketplace
claude plugin validate .         # Claude Code's own manifest check
```

Then **run the skill for real** in a fresh session, and say in the PR what you ran and what you changed because of it. Running it is where most problems show up: every skill in this repo had bugs that only appeared on a real run.
