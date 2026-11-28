# Skills Lab

Practical agent skills for software factory engineers, from the Qodo team.

These are skills we use every day and think other engineers will find useful. They are experiments, not a product: each one is small, runs on primary sources, and was run for real before it landed here. Qodo's product skills live in [qodo-skills](https://github.com/qodo-ai/qodo-skills).

Every skill follows the [Agent Skills](https://agentskills.io) format, so it works in Claude Code, Codex, Cursor and other agents that read `SKILL.md`.

## Skills

### stay-current

Native features keep replacing custom tooling. A skill that watched a pull request and fixed review comments was worth having until `/ship` and PR Steward shipped. These two skills keep you current and show you what to drop.

| Skill | What it does |
| --- | --- |
| [release-radar](skills/release-radar/SKILL.md) | Briefs you on what's new in Claude Code and Claude since you last checked: changelog releases with the fixes filtered out, docs pages added since your last check (which catch launches the changelog never mentions), built-in commands, release notes and launch posts. Remembers your last check, so each brief covers only what's new. |
| [native-overlap-audit](skills/native-overlap-audit/SKILL.md) | Lists your skills, commands, subagents, plugins and hooks, and compares each one with native Claude Code features: replace, slim down or keep, with the docs page or changelog line as evidence. Also finds skills installed more than once, where an old copy can shadow a newer one. Changes nothing. |

Try: "what's new in Claude Code?", or "which of my skills does Claude do natively now?"

## Install

**Claude Code** (updates through the plugin marketplace):

```
/plugin marketplace add qodo-ai/skills-lab
/plugin install stay-current@qodo-skills-lab
```

**Any agent**, with [skills.sh](https://skills.sh) (asks which agents to install into):

```bash
npx skills add qodo-ai/skills-lab -g
```

Update with `npx skills update -g`. Each skill also tells you when GitHub has a newer version.

**By hand:** copy a folder from `skills/` into `~/.claude/skills/` (or your agent's skills folder).

## Requirements

`bash`, `curl` and `python3`, plus network access to GitHub and `code.claude.com`. No API keys.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: one folder per skill, spec frontmatter only, and run the skill for real before you open the PR.

## License

[MIT](LICENSE)
