#!/usr/bin/env python3
"""List the custom tooling a Claude Code user has installed.

Prints every skill, command, subagent, plugin and hook, with enough of each
one to judge it without opening its file: owner, description, the start of
its instructions, git blob hash and modified date. Ends with the names that
are installed more than once.

Usage: inventory.py [--project DIR] [--json] [--excerpt N]

Reads files under ~/.claude and the project; changes nothing. Standard
library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
DESC_LIMIT = 300


def yaml_fields(block: str) -> dict[str, str]:
    """Top-level scalar keys of a YAML mapping, including folded (>) and literal (|) block values."""
    fields: dict[str, str] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        top = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        i += 1
        if not top:
            continue
        key, value = top.group(1), top.group(2).strip()
        if value[:1] in (">", "|") and set(value[1:]) <= set("+-0123456789"):
            body = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                body.append(lines[i].strip())
                i += 1
            joiner = "\n" if value[0] == "|" else " "
            fields[key] = joiner.join(body).strip()
        else:
            fields[key] = value.strip("\"'")
    return fields


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Top-level scalar keys of the YAML frontmatter, and the body after it."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not match:
        return {}, text
    return yaml_fields(match.group(1)), match.group(2)


def one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def git_blob_sha(data: bytes) -> str:
    """The hash `git hash-object` prints, so copies can be compared with git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def describe_file(path: Path, kind: str, source: str, owner: str, fallback_name: str, excerpt: int) -> dict:
    data = path.read_bytes()
    meta, body = split_frontmatter(data.decode("utf-8", errors="replace"))
    linked = next((p for p in (path, path.parent) if p.is_symlink()), None)
    return {
        "kind": kind,
        "name": meta.get("name", fallback_name),
        "source": source,
        "owner": owner,
        "description": one_line(meta.get("description", ""), DESC_LIMIT),
        "user_invoked_only": meta.get("disable-model-invocation", "") == "true",
        "excerpt": one_line(re.sub(r"```.*?```", " [code] ", body, flags=re.S), excerpt),
        "sha": git_blob_sha(data),
        "modified": dt.date.fromtimestamp(path.stat().st_mtime).isoformat(),
        "symlink_to": os.readlink(linked) if linked else "",
        "path": str(path),
    }


def scan_skills(root: Path, source: str, owner: str, rows: list[dict], excerpt: int) -> None:
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        skill_md = entry / "SKILL.md"
        if entry.name.startswith(".") or entry.name == "synced" or not skill_md.is_file():
            continue
        rows.append(describe_file(skill_md, "skill", source, owner, entry.name, excerpt))


def scan_markdown_dir(root: Path, kind: str, source: str, owner: str, rows: list[dict], excerpt: int) -> None:
    if not root.is_dir():
        return
    for md in sorted(root.rglob("*.md")):
        rows.append(describe_file(md, kind, source, owner, md.stem, excerpt))


def marketplace_sources() -> dict[str, str]:
    """marketplace name -> where it comes from (a GitHub repo, a URL or a local folder)."""
    try:
        known = json.loads((CLAUDE / "plugins" / "known_marketplaces.json").read_text())
    except (OSError, ValueError):
        return {}
    sources = {}
    for name, entry in known.items():
        src = entry.get("source", {}) if isinstance(entry, dict) else {}
        sources[name] = src.get("repo") or src.get("url") or src.get("path") or src.get("source", "")
    return sources


def scan_plugins(rows: list[dict], excerpt: int) -> None:
    try:
        plugins = json.loads((CLAUDE / "plugins" / "installed_plugins.json").read_text()).get("plugins", {})
    except (OSError, ValueError):
        return
    origins = marketplace_sources()
    for plugin_id, installs in sorted(plugins.items()):
        records = installs if isinstance(installs, list) else [installs] if isinstance(installs, dict) else []
        seen_paths: set[str] = set()
        for install in records:
            path = install.get("installPath", "") if isinstance(install, dict) else ""
            if path and path not in seen_paths:
                seen_paths.add(path)
                scan_plugin_install(plugin_id, install, origins, rows, excerpt)


def scan_plugin_install(plugin_id: str, install: dict, origins: dict[str, str], rows: list[dict], excerpt: int) -> None:
    """One installation of a plugin. A plugin can be installed more than once (user and project scope)."""
    root = Path(install.get("installPath", ""))
    marketplace = plugin_id.split("@", 1)[1] if "@" in plugin_id else ""
    owner = f"marketplace {marketplace}" + (f" ({origins[marketplace]})" if origins.get(marketplace) else "")
    description = ""
    try:
        description = json.loads((root / ".claude-plugin" / "plugin.json").read_text()).get("description", "")
    except (OSError, ValueError):
        pass
    scope = f" ({install['scope']} scope)" if install.get("scope") else ""
    rows.append({
        "kind": "plugin", "name": plugin_id, "source": f"plugin {install.get('version', '')}{scope}".strip(),
        "owner": owner, "description": one_line(description, DESC_LIMIT), "user_invoked_only": False,
        "excerpt": "", "sha": "", "modified": "", "symlink_to": "", "path": str(root),
    })
    source = f"plugin {plugin_id}{' ' + install['version'] if install.get('version') else ''}{scope}"
    scan_skills(root / "skills", source, owner, rows, excerpt)
    scan_markdown_dir(root / "commands", "command", source, owner, rows, excerpt)
    scan_markdown_dir(root / "agents", "agent", source, owner, rows, excerpt)
    hooks_file = root / "hooks" / "hooks.json"
    if hooks_file.is_file():
        scan_hooks(hooks_file, source, owner, rows)


def scan_hooks(settings: Path, source: str, owner: str, rows: list[dict]) -> None:
    try:
        hooks = json.loads(settings.read_text()).get("hooks", {})
    except (OSError, ValueError, AttributeError):
        return
    for event, groups in sorted(hooks.items()):
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []):
                command = hook.get("command") or hook.get("url") or hook.get("prompt") or hook.get("type", "")
                rows.append({
                    "kind": "hook", "name": f"{event} [{group.get('matcher', '*')}]", "source": source,
                    "owner": owner, "description": one_line(str(command), DESC_LIMIT), "user_invoked_only": False,
                    "excerpt": "", "sha": "", "modified": "", "symlink_to": "", "path": str(settings),
                })


def find_duplicates(rows: list[dict]) -> dict[str, list[dict]]:
    """Skills, commands and agents with the same name from more than one source.

    Copies of one skill drift apart: an old personal copy can keep loading
    after the plugin version has moved on. Names that only plugins share are
    left out, because plugin items are namespaced (plugin:name) and don't
    collide.
    """
    by_name: dict[str, list[dict]] = {}
    for row in rows:
        if row["kind"] in ("skill", "command", "agent"):
            by_name.setdefault(f"{row['kind']}:{row['name']}", []).append(row)
    return {
        key.replace(":", " ", 1): copies  # "skill qodo-review", "command review": the kind keeps groups apart
        for key, copies in sorted(by_name.items())
        if len({row["source"] for row in copies}) > 1
        and any(not row["source"].startswith("plugin") for row in copies)
    }


def print_markdown(rows: list[dict], project: Path) -> None:
    cell = lambda text: str(text).replace("|", "\\|")
    print(f"# Inventory: {len(rows)} items (project: {project})\n")
    print("Owner: `you` = personal, `project` = this repo, otherwise the plugin's marketplace.\n")
    print("| kind | name | source | owner | sha | modified | description |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        flags = " (user-invoked only)" if row["user_invoked_only"] else ""
        print(f"| {row['kind']} | {cell(row['name'])}{flags} | {cell(row['source'])} | {cell(row['owner'])} "
              f"| {row['sha'][:8]} | {row['modified']} | {cell(row['description'])} |")

    print("\n## What each skill, command and agent says it does\n")
    for row in rows:
        if row["excerpt"]:
            link = f" (symlink → {row['symlink_to']})" if row["symlink_to"] else ""
            print(f"### {row['name']} [{row['source']}]{link}\n{row['path']}\n\n{row['excerpt']}\n")

    print("## Installed more than once\n")
    duplicates = find_duplicates(rows)
    if not duplicates:
        print("None.")
    for name, copies in duplicates.items():
        identical = "identical" if len({c["sha"] for c in copies}) == 1 else "different contents"
        print(f"- `{name}` ({identical}):")
        ordered = sorted(copies, key=lambda c: c["modified"], reverse=True)
        for row in ordered:
            change = "" if row is ordered[0] else f", {diff_summary(row['path'], ordered[0]['path'])} vs the first copy listed"
            print(f"  - {row['source']}: sha {row['sha'][:8]}, file modified {row['modified']}{change}, {row['path']}")
        print("  - Order is by file time, which copying or reinstalling resets. Compare the contents and any plugin "
              "version before calling one copy newer.")


def diff_summary(old: str, new: str) -> str:
    """'+A −R lines' between two files, without printing their contents."""
    try:
        a = Path(old).read_text(errors="replace").splitlines()
        b = Path(new).read_text(errors="replace").splitlines()
    except OSError:
        return "not comparable"
    added = removed = 0
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return f"+{added} −{removed} lines"


def print_scanned(scanned: list[tuple[str, int]]) -> None:
    print("\n## Places checked\n")
    for place, count in scanned:
        print(f"- {place}: {count} item(s)" if count >= 0 else f"- {place}: not present")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=os.getcwd(), help="project folder (default: current folder)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--excerpt", type=int, default=600, help="characters of each item's instructions to show")
    args = parser.parse_args()
    project = Path(args.project).resolve()

    rows: list[dict] = []
    scanned: list[tuple[str, int]] = []

    def scan(place: Path, label: str, scanner, *scan_args) -> None:
        """Run one scanner and record how many items it found, or that the place is missing."""
        before = len(rows)
        if place.exists():
            scanner(place, *scan_args)
            scanned.append((f"{label} ({place})", len(rows) - before))
        else:
            scanned.append((f"{label} ({place})", -1))

    scan(CLAUDE / "skills", "personal skills", scan_skills, "personal", "you", rows, args.excerpt)
    scan(CLAUDE / "commands", "personal commands", scan_markdown_dir, "command", "personal", "you", rows, args.excerpt)
    scan(CLAUDE / "agents", "personal subagents", scan_markdown_dir, "agent", "personal", "you", rows, args.excerpt)
    if project != HOME:
        claude_dir = project / ".claude"
        scan(claude_dir / "skills", "project skills", scan_skills, "project", "project", rows, args.excerpt)
        scan(claude_dir / "commands", "project commands", scan_markdown_dir, "command", "project", "project", rows, args.excerpt)
        scan(claude_dir / "agents", "project subagents", scan_markdown_dir, "agent", "project", "project", rows, args.excerpt)
    before = len(rows)
    scan_plugins(rows, args.excerpt)
    scanned.append(("installed plugins, with their skills, commands, subagents and hooks", len(rows) - before))
    scan(CLAUDE / "settings.json", "personal hooks", scan_hooks, "personal settings", "you", rows)
    scan(project / ".claude" / "settings.json", "project hooks", scan_hooks, "project settings", "project", rows)
    scan(project / ".claude" / "settings.local.json", "local hooks", scan_hooks, "local settings", "you", rows)

    if args.json:
        json.dump({"items": rows, "duplicates": list(find_duplicates(rows))}, sys.stdout, indent=2)
        print()
    else:
        print_markdown(rows, project)
        print_scanned(scanned)
        print("\nNot covered: built-in skills and skills synced from claude.ai, which the user can't change, and plugins "
              "loaded only for one session with --plugin-dir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
