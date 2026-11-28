# Skills Lab

Practical agent skills for software factory engineers, from the Qodo team.

These are skills we use every day and think other engineers will find useful. They are experiments, not a product: each one is small, runs on primary sources, and was run for real before it landed here. Qodo's product skills live in [qodo-skills](https://github.com/qodo-ai/qodo-skills).

Every skill follows the [Agent Skills](https://agentskills.io) format, so it works in Claude Code, Codex, Cursor and other agents that read `SKILL.md`. No skill depends on another skill or on third-party tools: each one brings its own scripts, written with the Python standard library and bash.

## How we build skills

- **Scripts gather the facts, the model makes the call.** Anything deterministic (parsing, counting, diffing, fetching) is a script the skill pre-approves. The model spends its judgment on what a script can't decide, and the same step runs the same way every time.
- **Evidence over assertion.** Every finding cites a `file:line`, a docs passage or a command's output. "Looks like" isn't a finding.
- **The team's rules, not a textbook.** A skill judges a codebase against what that codebase says about itself (its `CLAUDE.md`, `REVIEW.md`, CI) before any general ideal.
- **Review the change.** When there's a diff, what it introduces is the verdict. Existing debt is context.
- **Prove it by running it.** Commands are run before they're recommended, and skills are trial-run before they ship.
- **Propose, don't impose.** Skills change nothing until you approve the diff.

## Skills

### stay-current

Native features keep replacing custom tooling. A skill that watched a pull request and fixed review comments was worth having until `/ship` and PR Steward shipped. These two skills keep you current and show you what to drop.

| Skill | What it does |
| --- | --- |
| [release-radar](skills/release-radar/SKILL.md) | Briefs you on what's new in Claude Code and Claude since you last checked: changelog releases with the fixes filtered out, docs pages added since your last check (which catch launches the changelog never mentions), built-in commands, release notes and launch posts. Remembers your last check, so each brief covers only what's new. |
| [native-overlap-audit](skills/native-overlap-audit/SKILL.md) | Lists your skills, commands, subagents, plugins and hooks, and compares each one with native Claude Code features: replace, slim down or keep, with the docs page or changelog line as evidence. Also finds skills installed more than once, where an old copy can shadow a newer one. Changes nothing. |

Try: "what's new in Claude Code?", or "which of my skills does Claude do natively now?"

### code-health

A codebase that's easy for agents to change is one where code lives in the right place and the checks are explicit.

| Skill | What it does |
| --- | --- |
| [structure-review](skills/structure-review/SKILL.md) | Maps the import graph (Python, JavaScript, TypeScript) and finds wrong-way dependencies between layers, package cycles with the cheapest edges to cut, reach-ins into other packages' internals and duplicated constants. Then judges each finding against your codebase's own documented conventions. Reviews a whole codebase or just what a change introduces. Changes nothing. |
| [agent-guardrails](skills/agent-guardrails/SKILL.md) | Finds the checks your CI actually enforces, proves each command runs today, records failures that already exist, and writes a short "before you hand back work" section into `AGENTS.md`. Also makes sure Claude Code reads it: with a `CLAUDE.md` that doesn't import `AGENTS.md`, Claude Code never sees it. |

Try: "review the structure of this repo", "does this PR break our module boundaries?", or "set up AGENTS.md so agents run our checks".

### skill-authoring

| Skill | What it does |
| --- | --- |
| [skill-lint](skills/skill-lint/SKILL.md) | Reviews agent skills the way they fail in practice. A lint script finds spec, portability and best-practice problems, including the `$1`/`$2` trap where Claude Code swaps a skill argument into your shell snippet. The model judges what a script can't, and a trial run in a fresh headless session reports every step that failed. Every skill in this repo went through it. |

Try: "lint my skills in ~/.claude/skills", or "is this skill ready to publish?"

## Install

**Claude Code** (updates through the plugin marketplace):

```
/plugin marketplace add qodo-ai/skills-lab
/plugin install stay-current@qodo-skills-lab
/plugin install code-health@qodo-skills-lab
/plugin install skill-authoring@qodo-skills-lab
```

**Any agent**, with [skills.sh](https://skills.sh) (asks which skills and agents to install into):

```bash
npx skills add qodo-ai/skills-lab -g
```

Update with `npx skills update -g`. The stay-current skills also tell you when GitHub has a newer version.

**By hand:** copy a folder from `skills/` into `~/.claude/skills/` (or your agent's skills folder).

## Requirements

`python3`, `bash` and `git`, plus network access to GitHub and `code.claude.com` for the stay-current skills. skill-lint's trial run uses the `claude` CLI. No API keys.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: one folder per skill, spec frontmatter only, no dependencies on other skills, and run the skill for real before you open the PR.

## License

[MIT](LICENSE)
