#!/usr/bin/env python3
"""Check every skill in this repo before it ships.

Run from the repo root: python3 scripts/validate.py
Exits 1 and lists the problems if any check fails. Standard library only.

Checks:
  - SKILL.md frontmatter follows the Agent Skills spec (agentskills.io), so the
    skill also loads in claude.ai, the Skills API and agents other than Claude
    Code: name and description are present and valid, name matches the folder,
    and no keys outside the spec are used.
  - The body has no positional placeholders such as $1. Claude Code replaces
    $N in a skill with the Nth argument, which breaks shell snippets like
    awk '{print $2}' without any error. Put shell logic in scripts/ instead.
  - Relative links and ${CLAUDE_SKILL_DIR}/... paths point at files that exist.
  - Files under scripts/ are executable.
  - Names avoid Anthropic's reserved words: skill names can't contain
    "claude" or "anthropic", and plugin names can't start with "claude-".
  - Every skill is in a plugin in .claude-plugin/marketplace.json, and every
    path listed there exists.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
RESERVED_WORDS = ("claude", "anthropic")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# $1, $2 ... but not ${VAR}, $ARGUMENTS or prices like "$5/month" in prose.
POSITIONAL_RE = re.compile(r"(?<![\w\\])\$[0-9]\b(?![./,]\d)")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        top = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if top:
            fields[top.group(1)] = top.group(2).strip().strip("\"'")
    return fields, match.group(2)


def check_skill(skill_dir: Path, errors: list[str]) -> None:
    rel = skill_dir.relative_to(ROOT)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        errors.append(f"{rel}: missing SKILL.md")
        return
    parsed = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if parsed is None:
        errors.append(f"{rel}/SKILL.md: no YAML frontmatter between --- lines")
        return
    fields, body = parsed

    name = fields.get("name", "")
    if not NAME_RE.match(name) or len(name) > 64:
        errors.append(f"{rel}/SKILL.md: name {name!r} must be 1-64 lowercase letters, digits and single hyphens")
    if any(word in name for word in RESERVED_WORDS):
        errors.append(f"{rel}/SKILL.md: name {name!r} contains a reserved word ({', '.join(RESERVED_WORDS)}); claude.ai and the Skills API reject it")
    if name != skill_dir.name:
        errors.append(f"{rel}/SKILL.md: name {name!r} must match the folder name {skill_dir.name!r}")
    description = fields.get("description", "")
    if not description:
        errors.append(f"{rel}/SKILL.md: description is required")
    elif len(description) > 1024:
        errors.append(f"{rel}/SKILL.md: description is {len(description)} characters; the limit is 1024")
    if len(fields.get("compatibility", "")) > 500:
        errors.append(f"{rel}/SKILL.md: compatibility is over 500 characters")
    extra = sorted(set(fields) - SPEC_KEYS)
    if extra:
        errors.append(f"{rel}/SKILL.md: keys outside the Agent Skills spec: {', '.join(extra)} (put them under metadata)")

    for number, line in enumerate(body.splitlines(), start=len(skill_md.read_text().splitlines()) - len(body.splitlines()) + 1):
        if POSITIONAL_RE.search(line):
            errors.append(f"{rel}/SKILL.md:{number}: '$<digit>' is replaced by a skill argument; move this into a script")

    for target in re.findall(r"\]\((?!https?:|#|mailto:)([^)#\s]+)", body):
        if not (skill_dir / target).exists():
            errors.append(f"{rel}/SKILL.md: link to missing file {target}")
    for target in re.findall(r"\$\{CLAUDE_SKILL_DIR\}/([\w./-]+)", body + fields.get("allowed-tools", "")):
        if not (skill_dir / target).exists():
            errors.append(f"{rel}/SKILL.md: ${{CLAUDE_SKILL_DIR}}/{target} does not exist")

    scripts = skill_dir / "scripts"
    if scripts.is_dir():
        for script in scripts.iterdir():
            if script.suffix in (".sh", ".py", ".mjs", ".js") and not os.access(script, os.X_OK):
                errors.append(f"{script.relative_to(ROOT)}: not executable (chmod +x, and commit the mode)")


def check_marketplace(skill_dirs: list[Path], errors: list[str]) -> None:
    path = ROOT / ".claude-plugin" / "marketplace.json"
    try:
        marketplace = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        errors.append(f".claude-plugin/marketplace.json: {error}")
        return
    listed: set[Path] = set()
    for plugin in marketplace.get("plugins", []):
        plugin_name = plugin.get("name", "")
        if plugin_name.startswith(("claude-", "anthropic-", "cc-plugin-")) or plugin_name in ("claude", "anthropic", "claude-code"):
            errors.append(f"marketplace.json: plugin name {plugin_name!r} is reserved for Anthropic; name it for what it does")
        for skill in plugin.get("skills", []):
            target = (ROOT / plugin.get("source", "./") / skill).resolve()
            if not (target / "SKILL.md").is_file():
                errors.append(f"marketplace.json: plugin {plugin.get('name')!r} lists {skill}, which has no SKILL.md")
            listed.add(target)
    for skill_dir in skill_dirs:
        if skill_dir.resolve() not in listed:
            errors.append(f"{skill_dir.relative_to(ROOT)}: not listed in any plugin in .claude-plugin/marketplace.json")


def main() -> int:
    errors: list[str] = []
    skill_dirs = sorted(p for p in SKILLS.iterdir() if p.is_dir() and not p.name.startswith("."))
    for skill_dir in skill_dirs:
        check_skill(skill_dir, errors)
    check_marketplace(skill_dirs, errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} problem(s) in {len(skill_dirs)} skill(s).")
        return 1
    print(f"OK: {len(skill_dirs)} skill(s) valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
