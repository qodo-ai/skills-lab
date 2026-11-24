#!/usr/bin/env python3
"""Look up native Claude Code features, verbatim, from the official docs.

Usage:
  native-docs.py search WORD [WORD ...]   docs pages whose title or summary match
  native-docs.py page PAGE [--max N]      a docs page as Markdown (PAGE is a URL or a slug like ship-command)
  native-docs.py changelog TERM           changelog lines that mention TERM, with their version

Quotes for an audit verdict should come from `page` and `changelog`, which
print the source text unchanged. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request

DOCS = "https://code.claude.com/docs/en/"
INDEX_URL = "https://code.claude.com/docs/llms.txt"
CHANGELOG_URL = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "native-overlap-audit skill"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def search(words: list[str]) -> int:
    entries = re.findall(r"^- \[([^\]]+)\]\((https:[^)]+)\):?\s*(.*)$", fetch(INDEX_URL), re.M)
    terms = [w.lower() for w in words]
    scored = []
    for title, url, summary in entries:
        haystack = f"{title} {summary}".lower()
        score = sum(haystack.count(t) for t in terms) + 2 * sum(t in title.lower() for t in terms)
        if score:
            scored.append((score, title, url, summary))
    scored.sort(key=lambda item: -item[0])
    if not scored:
        print(f"No docs page mentions: {' '.join(words)}. Try other words for the same job.")
    for _, title, url, summary in scored[:12]:
        print(f"- {title}: {url}\n  {summary}")
    return 0


def page(name: str, limit: int) -> int:
    url = name if name.startswith("http") else DOCS + name.removesuffix(".md")
    url = url if url.endswith(".md") else url + ".md"
    text = fetch(url)
    text = re.sub(r"\A(?:>.*\n)+\n?", "", text)  # the "Documentation Index" note on every page
    print(f"Source: {url.removesuffix('.md')}\n")
    if len(text) > limit:
        print(text[:limit])
        print(f"\n[Cut at {limit} of {len(text)} characters. Run again with --max {len(text)} for the rest.]")
    else:
        print(text)
    return 0


def changelog(term: str) -> int:
    version, hits = "", []
    for line in fetch(CHANGELOG_URL).splitlines():
        if line.startswith("## "):
            version = line[3:].strip()
        elif line.startswith("- ") and term.lower() in line.lower():
            hits.append(f"{version}: {line[2:]}")
    if not hits:
        print(f"No changelog line mentions {term!r}. Many features, including some CLI commands, ship with a docs page "
              "but no changelog line. Use the version or plan requirement stated on the docs page instead.")
        return 0
    print(f"{len(hits)} lines mention {term!r}, newest first. The last one is the earliest mention.\n")
    print("\n".join(hits[:40]))
    if len(hits) > 40:
        print(f"... {len(hits) - 40} more. Earliest: {hits[-1]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("search").add_argument("words", nargs="+")
    page_parser = sub.add_parser("page")
    page_parser.add_argument("name")
    page_parser.add_argument("--max", type=int, default=15000)
    sub.add_parser("changelog").add_argument("term")
    args = parser.parse_args()
    try:
        if args.command == "search":
            return search(args.words)
        if args.command == "page":
            return page(args.name, args.max)
        return changelog(args.term)
    except Exception as error:  # network or HTTP error: say what failed
        print(f"ERROR: {error.__class__.__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
