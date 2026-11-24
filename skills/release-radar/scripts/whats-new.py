#!/usr/bin/env python3
"""What changed in Claude Code and Claude since the last check.

Prints, verbatim from primary sources, for one date window:
  - installed Claude Code version vs. the latest release
  - Claude Code changelog releases in the window. "Fixed" lines are dropped,
    and "Improved" lines and lines for one surface ([VSCode], Windows: ...)
    are collapsed to counts, unless --all is given.
  - Claude Code docs pages added since the last check. New pages catch
    launches that never get a changelog line, such as /ship and Claude Ship.
  - Claude Platform (API) release notes in the window
  - Claude Apps release notes in the window

The window starts at the last check, or 7 days ago on the first run.

Usage:
  whats-new.py                 brief since the last check
  whats-new.py --days 30       brief for the last 30 days
  whats-new.py --since 2.1.300 brief since a given Claude Code version
  whats-new.py --all           keep every changelog line
  whats-new.py --mark-seen     record now as the last check

State lives outside the skill folder, so plugin updates don't erase it:
  ${RELEASE_RADAR_STATE_DIR:-~/.claude/release-radar}/state.json

Standard library only. Exit code 0 on success, 1 if the changelog can't be
fetched. Other sources that fail are reported in the output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

CHANGELOG_URL = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
DOCS_CHANGELOG_URL = "https://code.claude.com/docs/en/changelog.md"  # has release dates
DOCS_INDEX_URL = "https://code.claude.com/docs/llms.txt"
PLATFORM_NOTES_URL = "https://platform.claude.com/docs/en/release-notes/overview.md"
APPS_NOTES_URL = "https://support.claude.com/en/articles/12138966-release-notes"
STATE_DIR = Path(os.environ.get("RELEASE_RADAR_STATE_DIR", Path.home() / ".claude" / "release-radar"))
STATE_FILE = STATE_DIR / "state.json"
FIRST_RUN_DAYS = 7
HEADING = "\x00H:"
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"^(?:###\s+)?((?:{MONTHS}) \d{{1,2}}, \d{{4}})\s*$")
FIXED_RE = re.compile(r"^- (\[[^\]]+\] |[A-Za-z][A-Za-z -]{1,24}: )?Fixed\b")
IMPROVED_RE = re.compile(r"^- (\[[^\]]+\] )?Improved\b")
SURFACE_RE = re.compile(r"^- (\[[^\]]+\]|(?:Windows|Linux|macOS|WSL|Self-hosted runner|Remote Control|Gateway|Cloud sessions)):")


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (release-radar skill)"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def try_fetch(url: str) -> tuple[str | None, str]:
    try:
        return fetch(url), ""
    except Exception as error:  # network, HTTP or decode error: report it, keep going
        return None, f"unavailable ({error.__class__.__name__}: {error})"


def parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%B %d, %Y").date()


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def docs_pages(index: str) -> dict[str, str]:
    """url -> title, from the docs index."""
    return {m.group(2): m.group(1) for m in re.finditer(r"^- \[([^\]]+)\]\((https:[^)]+)\)", index, re.M)}


def release_dates(docs_changelog: str | None) -> dict[str, dt.date]:
    dates: dict[str, dt.date] = {}
    if not docs_changelog:
        return dates
    lines = docs_changelog.splitlines()
    for i, line in enumerate(lines):
        version = re.match(r"^\*\*(\d+\.\d+\.\d+)\*\*\s*$", line)
        if version:
            for after in lines[i + 1 : i + 4]:
                date = DATE_RE.match(after.strip())
                if date:
                    dates[version.group(1)] = parse_date(date.group(1))
                    break
    return dates


def releases(changelog: str) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for line in changelog.splitlines():
        if line.startswith("## "):
            out.append((line[3:].strip(), []))
        elif out and line.startswith("- "):
            out[-1][1].append(line)
    return out


def print_release(version: str, date: dt.date | None, lines: list[str], keep_all: bool) -> None:
    print(f"\n### {version}" + (f" ({date:%B %-d, %Y})" if date else ""))
    fixed = improved = 0
    surfaces: dict[str, int] = {}
    for line in lines:
        if keep_all:
            print(line)
        elif FIXED_RE.match(line):
            fixed += 1
        elif SURFACE_RE.match(line):
            tag = SURFACE_RE.match(line).group(1).strip("[]")
            surfaces[tag] = surfaces.get(tag, 0) + 1
        elif IMPROVED_RE.match(line):
            improved += 1
        else:
            print(line)
    hidden = []
    if fixed:
        hidden.append(f"{fixed} fixes")
    if improved:
        hidden.append(f"{improved} improvements")
    hidden += [f"{n} {tag}" for tag, n in sorted(surfaces.items(), key=lambda item: -item[1])]
    if hidden:
        print(f"- (not shown: {', '.join(hidden)}; use --all to see them)")


def dated_sections(lines: list[str], since: dt.date) -> list[tuple[dt.date, list[str]]]:
    """Group lines under the date headings that precede them; keep dates from `since` on."""
    sections: list[tuple[dt.date, list[str]]] = []
    for line in lines:
        date = DATE_RE.match(line.strip())
        if date:
            sections.append((parse_date(date.group(1)), []))
        elif sections and line.strip():
            sections[-1][1].append(line.rstrip())
    return [(d, body) for d, body in sections if d >= since]


def platform_notes(text: str, since: dt.date) -> list[tuple[dt.date, list[str]]]:
    body = text.split("\n---\n", 2)[-1]
    return dated_sections(body.splitlines(), since)


def mark_heading(match: re.Match) -> str:
    """Prefix an item heading with HEADING; leave date headings as plain lines."""
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).split())
    return f"\n{text}\n" if DATE_RE.match(text) else f"\n{HEADING}{text}\n"


def apps_notes(page: str, since: dt.date) -> list[tuple[dt.date, list[str]]]:
    page = re.sub(r"(?s)<(script|style|nav|header|footer)\b.*?</\1>", "", page)
    # Mark each item's heading so it isn't joined to the text around it.
    # Dates are <h3> headings; each item's title is a paragraph of bold text.
    page = re.sub(r"(?is)<h[3-6]\b[^>]*>(.*?)</h[3-6]>", mark_heading, page)
    page = re.sub(r"(?is)<p>\s*<(?:b|strong)>(.*?)</(?:b|strong)>\s*</p>", mark_heading, page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|h[1-6]|li|div)>", "\n", page)
    text = html.unescape(re.sub(r"<[^>]+>", "", page))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    # Join the pieces of a sentence that inline links split over several lines.
    joined: list[str] = []
    for line in lines:
        if joined and not DATE_RE.match(line) and not DATE_RE.match(joined[-1]) and not line.startswith(HEADING) and not joined[-1].startswith(HEADING) and (
            not re.search(r"[.!?:)]$", joined[-1]) or line[:1].islower() or line[:1] in ".,;)"
        ):
            joined[-1] = f"{joined[-1]} {line}".replace(" .", ".").replace(" ,", ",")
        else:
            joined.append(line)
    return dated_sections(joined, since)


def print_notes(title: str, url: str, sections: list[tuple[dt.date, list[str]]] | None, error: str) -> None:
    print(f"\n## {title}\n\nSource: {url}")
    if sections is None:
        print(error)
    elif not sections:
        print("Nothing in the window.")
    for date, body in sections or []:
        print(f"\n### {date:%B %-d, %Y}")
        for line in body:
            if line.startswith(HEADING):
                print(f"- **{line[len(HEADING):].strip()}**")
            elif line.startswith(("*", "-")):
                print(line)
            else:
                print(f"  {line}")


def installed_version() -> str:
    if not shutil.which("claude"):
        return ""
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"\d+\.\d+\.\d+", out)
    return match.group(0) if match else ""


def mark_seen(changelog: str) -> int:
    index, error = try_fetch(DOCS_INDEX_URL)
    state = load_state()
    state["last_seen_version"] = releases(changelog)[0][0]
    state["checked_at"] = dt.date.today().isoformat()
    if index:
        state["docs_pages"] = sorted(docs_pages(index))
    else:
        print(f"WARN: docs index {error}; docs baseline not updated", file=sys.stderr)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    print(f"Marked seen: Claude Code {state['last_seen_version']} on {state['checked_at']}, "
          f"{len(state.get('docs_pages', []))} docs pages ({STATE_FILE})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="Claude Code version to brief from (exclusive)")
    parser.add_argument("--days", type=int, help="brief the last N days instead of since the last check")
    parser.add_argument("--all", action="store_true", help="keep every changelog line")
    parser.add_argument("--mark-seen", action="store_true", help="record now as the last check")
    args = parser.parse_args()

    changelog, error = try_fetch(CHANGELOG_URL)
    if changelog is None:
        print(f"ERROR: Claude Code changelog {error}: {CHANGELOG_URL}", file=sys.stderr)
        return 1
    if args.mark_seen:
        return mark_seen(changelog)

    state = load_state()
    today = dt.date.today()
    if args.days:
        window_start, basis = today - dt.timedelta(days=args.days), f"the last {args.days} days"
    elif state.get("checked_at"):
        window_start, basis = dt.date.fromisoformat(state["checked_at"]), f"the last check on {state['checked_at']}"
    else:
        window_start, basis = today - dt.timedelta(days=FIRST_RUN_DAYS), f"first run, so the last {FIRST_RUN_DAYS} days"
    since_version = args.since or (None if args.days else state.get("last_seen_version"))

    all_releases = releases(changelog)
    head = all_releases[0][0] if all_releases else "unknown"
    dates = release_dates(try_fetch(DOCS_CHANGELOG_URL)[0])
    installed = installed_version()

    print("# What's new in Claude\n")
    print(f"- Window: from {window_start:%B %-d, %Y} ({basis}). A day on the window's edge can repeat items from the last brief.")
    print(f"- Latest Claude Code: {head}" + (f" ({dates[head]:%B %-d, %Y})" if head in dates else ""))
    print(f"- Installed Claude Code: {installed or 'not found on PATH'}")
    if installed and installed != head:
        print(f"- Your build is behind: items newer than {installed} may not be available yet (`claude update`).")

    print("\n## Claude Code changelog\n\nSource: " + CHANGELOG_URL)
    shown = 0
    for version, lines in all_releases:
        if since_version and version == since_version:
            break
        if not since_version and version in dates and dates[version] < window_start:
            break
        if not since_version and version not in dates and shown >= 5:
            break  # no dates available: fall back to 5 releases
        print_release(version, dates.get(version), lines, args.all)
        shown += 1
        if shown >= 60:
            print("\n(Stopped after 60 releases.)")
            break
    if shown == 0:
        print("No new releases.")

    print("\n## New Claude Code docs pages\n\nSource: " + DOCS_INDEX_URL)
    if not state.get("docs_pages"):
        print("No baseline yet: after this brief, run with --mark-seen, and later briefs list pages added since.")
    else:
        index, error = try_fetch(DOCS_INDEX_URL)
        if index is None:
            print(f"Docs index {error}.")
        else:
            pages = docs_pages(index)
            added = sorted(set(pages) - set(state["docs_pages"]))
            print("\n".join(f"- {pages[url]}: {url}" for url in added) if added else "None since the last check.")

    text, error = try_fetch(PLATFORM_NOTES_URL)
    print_notes("Claude Platform release notes (API, SDKs, Console)", PLATFORM_NOTES_URL,
                platform_notes(text, window_start) if text else None, error)
    page, error = try_fetch(APPS_NOTES_URL)
    print_notes("Claude Apps release notes", APPS_NOTES_URL,
                apps_notes(page, window_start) if page else None, error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
