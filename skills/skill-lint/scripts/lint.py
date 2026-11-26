#!/usr/bin/env python3
"""Lint agent skills: the mechanical checks, so a review can focus on judgment.

Usage:
  lint.py PATH [PATH ...] [--json]

PATH is a skill folder (with SKILL.md) or a folder of skill folders, such as
~/.claude/skills or a repo's skills/. Exit code 1 if any skill has an error.

Levels:
  error        the skill breaks or doesn't load as written
  portability  loads in Claude Code, but claude.ai, the Skills API or other
               agents reject it or behave differently
  warning      a best-practice gap (platform.claude.com skill best practices)

Standard library only. Reads files; changes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
# Frontmatter keys Claude Code documents beyond the spec (code.claude.com/docs/en/skills, checked 2026-11-26).
CLAUDE_CODE_KEYS = {
    "when_to_use", "argument-hint", "arguments", "disable-model-invocation", "user-invocable",
    "disallowed-tools", "model", "effort", "context", "agent", "background", "hooks", "paths", "shell",
}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED = ("claude", "anthropic")
# $1, $2 ... are replaced with skill arguments by Claude Code. Not ${VAR}, $ARGUMENTS or "$5/month".
POSITIONAL_RE = re.compile(r"(?<![\w\\$])\$[0-9]\b(?![./,]\d)")
SKILL_DIR_RE = re.compile(r"\$\{CLAUDE_SKILL_DIR\}/([\w./-]+)")
LINK_RE = re.compile(r"\]\((?!https?:|#|mailto:)([^)#\s]+)")
SCRIPT_SUFFIXES = (".sh", ".bash", ".py", ".js", ".mjs", ".ts", ".rb")


def parse(text: str) -> tuple[dict[str, str], str, int] | None:
    """Frontmatter top-level keys, the body, and the line number where the body starts."""
    match = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*\n?", text, re.S)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        top = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if top:
            fields[top.group(1)] = top.group(2).strip().strip("\"'")
    return fields, text[match.end():], match.group(0).count("\n") + 1


def in_code(body_lines: list[str]) -> list[bool]:
    """For each line, whether it sits inside a fenced code block."""
    flags, fenced = [], False
    for line in body_lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
            flags.append(True)
        else:
            flags.append(fenced)
    return flags


def lint_skill(skill: Path) -> list[dict]:
    findings: list[dict] = []

    def add(level: str, code: str, message: str, line: int | None = None) -> None:
        findings.append({"level": level, "code": code, "message": message, "line": line})

    skill_md = skill / "SKILL.md"
    if not skill_md.is_file():
        add("error", "no-skill-md", "no SKILL.md in this folder")
        return findings
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    parsed = parse(text)
    if parsed is None:
        add("error", "no-frontmatter", "SKILL.md has no YAML frontmatter between --- lines")
        return findings
    fields, body, body_start = parsed

    # Frontmatter
    name, description = fields.get("name", ""), fields.get("description", "")
    if not name:
        add("warning", "no-name", "no `name`: Claude Code falls back to the folder name; claude.ai and the API require it")
    else:
        if not NAME_RE.match(name) or len(name) > 64:
            add("error", "bad-name", f"name {name!r} must be 1-64 lowercase letters, digits and single hyphens")
        if name != skill.name:
            add("portability", "name-mismatch", f"name {name!r} differs from the folder {skill.name!r}; the spec requires them to match")
        hits = [w for w in RESERVED if w in name]
        if hits:
            add("portability", "reserved-name", f"name contains {', '.join(hits)}; claude.ai and the Skills API reject it")
    if not description:
        add("error", "no-description", "no `description`: agents decide whether to load a skill from it")
    elif len(description) > 1024:
        add("portability", "long-description", f"description is {len(description)} characters; the spec limit is 1024")
    if len(fields.get("compatibility", "")) > 500:
        add("portability", "long-compatibility", "compatibility is over 500 characters; the spec limit is 500")
    extra = sorted(set(fields) - SPEC_KEYS)
    cc_only = [k for k in extra if k in CLAUDE_CODE_KEYS]
    unknown = [k for k in extra if k not in CLAUDE_CODE_KEYS]
    if cc_only:
        add("portability", "claude-code-keys", f"Claude Code-only keys: {', '.join(cc_only)}; claude.ai and the Skills API reject them")
    if unknown:
        add("warning", "unknown-keys", f"keys no agent reads: {', '.join(unknown)} (put custom data under `metadata`)")
    if description:
        if not re.search(r"\b(use|used|invoke|trigger)\w*\b.{0,80}?\b(when|on|for|after|before|if|whenever)\b"
                         r"|\bwhen (asked|the user|you)\b", description, re.I):
            add("warning", "no-trigger", "description doesn't say when to use the skill (e.g. 'Use when asked …')")
        if re.match(r"^(I|I'm|You|Your|We|Use this skill to)\b", description):
            add("warning", "description-person", "description should be in the third person ('Builds …', not 'I/You …')")

    broad = [t for t in re.findall(r"\b(Bash|Write|Edit|WebFetch|NotebookEdit)\b(?!\()", fields.get("allowed-tools", ""))
             if t != "WebFetch"]
    if broad:
        add("warning", "broad-allowed-tools", f"allowed-tools pre-approves {', '.join(sorted(set(broad)))} without a scope; "
            "that removes the prompt that would catch a mistake. Scope it, e.g. Bash(${CLAUDE_SKILL_DIR}/scripts/x.sh *)")

    # Body
    lines = body.splitlines()
    code = in_code(lines)
    if len(lines) > 500:
        add("warning", "long-body", f"body is {len(lines)} lines; best practice is under 500, with details in separate files")
    for i, line in enumerate(lines):
        number = body_start + i
        if POSITIONAL_RE.search(line):
            add("error", "positional-arg", "'$<digit>' is replaced by a skill argument in Claude Code (breaks shell like awk '{print $2}'); move the logic into a script",
                number)
        if re.search(r">>?\s*\"?\$\{CLAUDE_SKILL_DIR\}", line):
            add("warning", "state-in-skill-dir", "writes into the skill folder; plugin updates replace it, so keep state under ~/.claude/", number)
        if not code[i] and re.search(r"(?<![\w/])[A-Za-z]:\\|scripts\\\w", line):
            add("warning", "windows-path", "Windows-style path; use forward slashes", number)

    # Files the body points at
    referenced: set[Path] = set()
    for target in SKILL_DIR_RE.findall(body + "\n" + fields.get("allowed-tools", "")):
        path = skill / target.rstrip(".")
        referenced.add(path)
        if not path.exists():
            add("error", "missing-file", f"${{CLAUDE_SKILL_DIR}}/{target} does not exist")
    for target in LINK_RE.findall(body):
        path = skill / target
        if not path.exists():
            add("error", "broken-link", f"link to missing file {target}")
        elif path.suffix == ".md" and path.is_file():
            nested = [t for t in LINK_RE.findall(path.read_text(errors="replace")) if (path.parent / t).suffix == ".md"]
            if nested:
                add("warning", "nested-reference", f"{target} links on to {nested[0]}; keep references one level deep from SKILL.md")

    # Scripts
    scripts = [p for p in skill.rglob("*") if p.is_file() and p.suffix in SCRIPT_SUFFIXES]
    for script in scripts:
        rel = script.relative_to(skill)
        content = script.read_text(errors="replace")
        directly_run = script in referenced and not re.search(rf"(python3?|bash|sh|node)\s+\$\{{CLAUDE_SKILL_DIR\}}/{re.escape(str(rel))}", body)
        if directly_run and not os.access(script, os.X_OK):
            add("error", "not-executable", f"{rel} is run directly but isn't executable (chmod +x)")
        if content.startswith("#!") and not os.access(script, os.X_OK):
            add("warning", "shebang-not-executable", f"{rel} has a shebang but isn't executable")
        head = "\n".join(content.splitlines()[:12])
        if not re.search(r'^\s*(#(?!!)|//|"""|\'\'\'|/\*)', head, re.M):
            add("warning", "undocumented-script", f"{rel} has no header comment saying what it does and how to call it")
        if script.suffix in (".sh", ".bash") and "pipefail" in content and re.search(
                r"\|\s*(awk\b[^|;]{0,600}?\bexit\b|head\b|grep -m|sed -n '?1q)", content, re.S):
            add("warning", "pipefail-sigpipe", f"{rel}: with pipefail, piping into a command that exits early (awk … exit, head) "
                "gives the writer SIGPIPE, and set -e ends the script silently; use a here-string or a file")
    return findings


def find_skills(path: Path) -> list[Path]:
    if (path / "SKILL.md").is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.iterdir() if (p / "SKILL.md").is_file() and not p.name.startswith("."))
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report: dict[str, list[dict]] = {}
    for raw in args.paths:
        path = Path(raw).expanduser()
        skills = find_skills(path)
        if not skills:
            report[str(path)] = [{"level": "error", "code": "no-skills", "message": "no SKILL.md here or in its subfolders", "line": None}]
        for skill in skills:
            report[str(skill)] = lint_skill(skill)

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        order = {"error": 0, "portability": 1, "warning": 2}
        for skill, findings in report.items():
            counts = {level: sum(f["level"] == level for f in findings) for level in order}
            print(f"\n## {skill}  ({counts['error']} errors, {counts['portability']} portability, {counts['warning']} warnings)")
            for f in sorted(findings, key=lambda f: order[f["level"]]):
                where = f" (SKILL.md:{f['line']})" if f["line"] else ""
                print(f"- {f['level']} [{f['code']}]{where}: {f['message']}")
        total = {level: sum(f["level"] == level for fs in report.values() for f in fs) for level in order}
        print(f"\n{len(report)} skill(s): {total['error']} errors, {total['portability']} portability, {total['warning']} warnings")
    return 1 if any(f["level"] == "error" for fs in report.values() for f in fs) else 0


if __name__ == "__main__":
    sys.exit(main())
