#!/usr/bin/env python3
"""Find the checks a repository actually enforces, with evidence for each one.

Looks at CI configs, package.json scripts, Makefile/justfile targets, Python
tool configs, pre-commit hooks, Cargo and Go, and at the instruction files
agents read (AGENTS.md, CLAUDE.md). For every candidate command it reports:
  kind      format / lint / typecheck / check / test / build
  source    file:line it came from
  in CI     whether a CI config runs it (the bar a PR must pass)
  writes    whether it rewrites files (--fix, --write, formatters without --check)

Usage: find-checks.py [REPO] [--json]

Standard library only. Reads files; runs nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KINDS = {
    "format": r"\b(fmt|format|prettier|black|isort|gofmt|rustfmt|ruff format)\b",
    "lint": r"\b(lint|eslint|ruff|flake8|pylint|clippy|golangci|vet|shellcheck|stylelint|biome)\b",
    "typecheck": r"\b(typecheck|type-check|tsc|mypy|pyright|check-types)\b",
    "test": r"\b(test|tests|pytest|jest|vitest|mocha|go test|cargo test|unittest)\b",
    "build": r"\b(build|compile)\b",
    "check": r"\b(validate|verify|check)\b",
}
NOT_A_COMMAND = re.compile(r"^(echo|printf|export|cd|if|then|else|fi|for|do|done|set|source|\.|\w+=|#)\b|^[A-Z_][A-Z0-9_]*=")
WRITES = re.compile(r"(--fix\b|--write\b|\s-w\b|--apply\b|ruff format(?!.*--check)|black(?!.*--check)|"
                    r"prettier(?!.*--check)(?=.*--write)|gofmt -w|cargo fmt(?!.*--check)|isort(?!.*--check))")
# Script names that set things up or open a UI rather than check anything.
NOT_A_CHECK = re.compile(r"(^|:)(setup|ui|watch|dev|serve|start|open|postinstall|preinstall|prepare|install)$|^(post|pre)install$"
                         r"|(^|:)(deploy|publish|release)\b")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".tox", "target"}


TOOL_KINDS = {
    "black": "format", "isort": "format", "prettier": "format", "gofmt": "format", "rustfmt": "format",
    "ruff": "lint", "eslint": "lint", "flake8": "lint", "pylint": "lint", "shellcheck": "lint", "stylelint": "lint",
    "biome": "lint", "golangci-lint": "lint", "mypy": "typecheck", "pyright": "typecheck", "tsc": "typecheck",
    "pytest": "test", "jest": "test", "vitest": "test", "mocha": "test", "tox": "test", "nox": "test",
    "pre-commit": "lint",
}
VALUE_FLAGS = {"--group", "--with", "--python", "-p", "--project", "--directory", "--extra", "--package",
               "--only-group", "--env-file", "-C", "--cwd", "--filter", "-F", "-w", "--workspace"}
WRAPPERS = {("npx",), ("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("python", "-m"), ("python3", "-m"),
            ("bundle", "exec"), ("pnpm", "exec"), ("yarn", "dlx"), ("bunx",)}


def ci_kind(command: str) -> str | None:
    """Kind of a CI command line, judged by the program it runs, not by any word on the line."""
    tokens = command.replace("&&", " ").split()
    while tokens and re.match(r"^[A-Z_][A-Z0-9_]*=", tokens[0]):
        tokens.pop(0)
    changed = True
    while changed and tokens:
        changed = False
        for wrapper in WRAPPERS:
            if tuple(tokens[:len(wrapper)]) == wrapper:
                tokens, changed = tokens[len(wrapper):], True
                # Skip the wrapper's own flags (uv run --locked --group dev …) and their values.
                while tokens and tokens[0].startswith("-"):
                    flag = tokens.pop(0)
                    if "=" not in flag and flag in VALUE_FLAGS and tokens:
                        tokens.pop(0)
    if not tokens:
        return None
    program = tokens[0].rsplit("/", 1)[-1]
    rest = tokens[1:]
    if program in TOOL_KINDS:
        if program == "ruff" and rest[:1] == ["format"]:
            return "format"
        if program in ("tox", "nox") and ("-e" in rest or "-s" in rest):
            env = rest[min(i for i, t in enumerate(rest) if t in ("-e", "-s")) + 1:][:1]
            return (kind_of(env[0]) or ("typecheck" if env and env[0] in ("typing", "types") else None)
                    or TOOL_KINDS[program]) if env else TOOL_KINDS[program]
        return TOOL_KINDS[program]
    if program in ("make", "just") and rest:
        return kind_of(rest[0])
    if program in ("npm", "pnpm", "yarn", "bun") and rest:
        script = rest[1] if rest[0] == "run" and len(rest) > 1 else rest[0]
        if script in ("install", "ci", "i", "add") or NOT_A_CHECK.search(script):
            return None
        return kind_of(script)
    if program == "go" and rest:
        return {"test": "test", "vet": "lint", "fmt": "format", "build": "build"}.get(rest[0])
    if program == "cargo" and rest:
        return {"test": "test", "clippy": "lint", "fmt": "format", "build": "build", "check": "typecheck"}.get(rest[0])
    if program in ("python", "python3", "node", "bash", "sh") and rest and "/" in rest[0]:
        name = rest[0].rsplit("/", 1)[-1]
        return kind_of(name) if re.search(r"(lint|check|validate|verify|test|typecheck)", name) else None
    return None


def kind_of(text: str) -> str | None:
    for kind, pattern in KINDS.items():
        if re.search(pattern, text, re.I):
            return kind
    return None


def rel(repo: Path, path: Path) -> str:
    return str(path.relative_to(repo))


def ci_commands(repo: Path) -> list[dict]:
    """`run:` / `script:` lines from CI configs, one entry per command line."""
    files = list((repo / ".github" / "workflows").glob("*.y*ml")) + [
        p for p in (repo / ".gitlab-ci.yml", repo / "bitbucket-pipelines.yml", repo / ".circleci" / "config.yml") if p.is_file()]
    out = []
    for path in files:
        lines = path.read_text(errors="replace").splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r"^(\s*)-?\s*(run|script|- run):\s*(.*)$", line) or re.match(r"^(\s*)-\s+(run):\s*(.*)$", line)
            if m:
                indent, value = len(m.group(1)), m.group(3).strip()
                block = []
                if value in ("|", ">", "|-", ">-", ""):
                    j = i + 1
                    while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                        if lines[j].strip() and not lines[j].strip().startswith("#"):
                            block.append((j + 1, re.sub(r"^-\s+", "", lines[j].strip())))
                        j += 1
                    i = j - 1
                else:
                    block.append((i + 1, value.strip("'\"")))
                joined: list[tuple[int, str]] = []
                for number, cmd in block:
                    if joined and joined[-1][1].endswith("\\"):
                        joined[-1] = (joined[-1][0], joined[-1][1][:-1].rstrip() + " " + cmd)
                    else:
                        joined.append((number, cmd))
                for number, cmd in joined:
                    if not NOT_A_COMMAND.match(cmd):
                        out.append({"command": cmd, "source": f"{rel(repo, path)}:{number}"})
            i += 1
    return out


def package_manager(repo: Path) -> str:
    for lock, pm in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun")):
        if (repo / lock).exists():
            return pm
    return "npm"


def node_scripts(repo: Path) -> list[dict]:
    out = []
    for pkg in [repo / "package.json"]:
        if not pkg.is_file():
            continue
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except ValueError:
            continue
        pm = package_manager(repo)
        text = pkg.read_text().splitlines()
        for name, body in scripts.items():
            kind = kind_of(name) or kind_of(body)
            if not kind or NOT_A_CHECK.search(name) or re.match(r"^\s*(playwright|cypress) install\b", body) \
                    or re.search(r"\b(deploy|publish)\b", body):
                continue
            number = next((n for n, l in enumerate(text, 1) if f'"{name}"' in l), 0)
            out.append({"command": f"{pm} run {name}", "runs": body, "kind": kind,
                        "source": f"{rel(repo, pkg)}:{number}"})
    return out


def make_targets(repo: Path) -> list[dict]:
    out = []
    for name, runner in (("Makefile", "make"), ("justfile", "just"), ("Justfile", "just")):
        path = repo / name
        if not path.is_file():
            continue
        lines = path.read_text(errors="replace").splitlines()
        for number, line in enumerate(lines, 1):
            m = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", line)
            if m and not m.group(1).startswith(".") and kind_of(m.group(1)):
                recipe = []
                for body in lines[number:]:
                    if body.startswith(("\t", "    ")) and body.strip():
                        recipe.append(body.strip().lstrip("@-"))
                    elif body.strip():
                        break
                out.append({"command": f"{runner} {m.group(1)}", "kind": kind_of(m.group(1)),
                            "runs": " && ".join(recipe)[:300], "source": f"{name}:{number}"})
    return out


def python_tools(repo: Path) -> list[dict]:
    out = []
    pyproject = repo / "pyproject.toml"
    configs = {
        "ruff": ("ruff check .", "[tool.ruff"), "black": ("black --check .", "[tool.black"),
        "mypy": ("mypy .", "[tool.mypy"), "pyright": ("pyright", "[tool.pyright"),
        "pytest": ("pytest", "[tool.pytest"), "isort": ("isort --check-only .", "[tool.isort"),
    }
    if pyproject.is_file():
        lines = pyproject.read_text(errors="replace").splitlines()
        for tool, (cmd, marker) in configs.items():
            number = next((n for n, l in enumerate(lines, 1) if l.strip().startswith(marker)), 0)
            if number:
                out.append({"command": cmd, "kind": kind_of(cmd), "source": f"pyproject.toml:{number}",
                            "note": "configured, command assumed"})
    for name, cmd in (("tox.ini", "tox"), ("noxfile.py", "nox"), ("pytest.ini", "pytest"), (".flake8", "flake8")):
        if (repo / name).is_file():
            out.append({"command": cmd, "kind": kind_of(cmd) or "test", "source": f"{name}:1",
                        "note": "configured, command assumed"})
    return out


def precommit(repo: Path) -> list[dict]:
    path = repo / ".pre-commit-config.yaml"
    if not path.is_file():
        return []
    out = []
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        m = re.match(r"^\s*-\s*id:\s*([\w.-]+)", line)
        if m:
            out.append({"command": f"pre-commit run {m.group(1)} --all-files", "kind": kind_of(m.group(1)) or "lint",
                        "source": f"{rel(repo, path)}:{number}"})
    return out


def native(repo: Path) -> list[dict]:
    out = []
    if (repo / "Cargo.toml").is_file():
        out += [{"command": c, "kind": kind_of(c), "source": "Cargo.toml:1", "note": "Rust defaults"}
                for c in ("cargo fmt --check", "cargo clippy -- -D warnings", "cargo test")]
    if (repo / "go.mod").is_file():
        out += [{"command": c, "kind": kind_of(c), "source": "go.mod:1", "note": "Go defaults"}
                for c in ("gofmt -l .", "go vet ./...", "go test ./...")]
    return out


def commit_hooks(repo: Path) -> list[dict]:
    """Commands that git hooks run on commit (husky, lint-staged, pre-commit). They often rewrite files."""
    hooks = []
    for path in sorted((repo / ".husky").glob("*")) if (repo / ".husky").is_dir() else []:
        if path.is_file() and not path.name.startswith(("_", ".")):
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if line.strip() and not line.startswith(("#", ".", "set ")):
                    hooks.append({"hook": f".husky/{path.name}", "command": line.strip(), "source": f".husky/{path.name}:{number}"})
    staged = None
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            staged = json.loads(pkg.read_text()).get("lint-staged")
        except ValueError:
            staged = None
    for name in (".lintstagedrc", ".lintstagedrc.json"):
        if staged is None and (repo / name).is_file():
            try:
                staged = json.loads((repo / name).read_text())
            except ValueError:
                pass
    for pattern, cmds in (staged or {}).items():
        for cmd in cmds if isinstance(cmds, list) else [cmds]:
            hooks.append({"hook": "lint-staged", "command": f"{cmd}  (on {pattern})", "source": "package.json lint-staged"})
    if (repo / ".pre-commit-config.yaml").is_file():
        hooks.append({"hook": "pre-commit", "command": "pre-commit run (hooks listed above)", "source": ".pre-commit-config.yaml"})
    for h in hooks:
        h["writes"] = bool(WRITES.search(h["command"]))
    return hooks


def instruction_files(repo: Path) -> dict:
    found = {name: (repo / name).is_file() for name in ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")}
    claude_md = next((repo / n for n in ("CLAUDE.md", ".claude/CLAUDE.md") if (repo / n).is_file()), None)
    imports_agents = bool(claude_md and re.search(r"(?m)^\s*@\.?/?AGENTS\.md\b", claude_md.read_text(errors="replace")))
    agents = repo / "AGENTS.md"
    has_section = agents.is_file() and "agent-guardrails:start" in agents.read_text(errors="replace")
    if found["AGENTS.md"] and claude_md and not imports_agents:
        reads = "CLAUDE.md only: Claude Code ignores AGENTS.md because CLAUDE.md exists and doesn't import it"
    elif found["AGENTS.md"] and claude_md:
        reads = "CLAUDE.md, which imports AGENTS.md"
    elif found["AGENTS.md"]:
        reads = "AGENTS.md (Claude Code 2.1.277+ reads it when there is no CLAUDE.md)"
    elif claude_md:
        reads = "CLAUDE.md (other agents such as Codex don't read it)"
    else:
        reads = "nothing yet"
    return {"files": found, "claude_md_imports_agents_md": imports_agents,
            "guardrails_section_present": has_section, "what_claude_code_reads": reads}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    ci = ci_commands(repo)
    ci_text = "\n".join(c["command"] for c in ci)
    candidates = node_scripts(repo) + make_targets(repo) + python_tools(repo) + precommit(repo) + native(repo)
    for c in ci:
        kind = ci_kind(c["command"])
        if kind:
            candidates.append({"command": c["command"], "kind": kind, "source": c["source"], "from_ci": True})
    seen, checks = set(), []
    for c in candidates:
        if c["command"] in seen:
            continue
        seen.add(c["command"])
        name = c["command"].split()[-1] if c["command"].split()[0] in ("make", "just") else None
        c["in_ci"] = bool(c.get("from_ci")) or c["command"] in ci_text or bool(name and re.search(rf"\b(make|just) {re.escape(name)}\b", ci_text)) \
            or bool(c.get("runs") and c["runs"] in ci_text)
        c["writes"] = bool(WRITES.search(c["command"] + " " + c.get("runs", "")))
        checks.append(c)
    order = ["format", "lint", "typecheck", "check", "test", "build"]
    checks.sort(key=lambda c: (order.index(c["kind"]), not c["in_ci"]))
    result = {"repo": str(repo), "instructions": instruction_files(repo), "checks": checks, "hooks": commit_hooks(repo),
              "ci_files": sorted({c["source"].split(":")[0] for c in ci})}

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
        return 0
    info = result["instructions"]
    print(f"# Checks in {repo.name}\n")
    print(f"- Instruction files: {', '.join(n for n, ok in info['files'].items() if ok) or 'none'}")
    print(f"- What Claude Code reads: {info['what_claude_code_reads']}")
    print(f"- Existing agent-guardrails section in AGENTS.md: {'yes' if info['guardrails_section_present'] else 'no'}")
    print(f"- CI configs: {', '.join(result['ci_files']) or 'none found'}\n")
    print("| kind | command | in CI | writes files | source | note |")
    print("|---|---|---|---|---|---|")
    for c in checks:
        note = c.get("note", "") or (f"runs: {c['runs']}" if c.get("runs") else "")
        command = c["command"].replace("|", "\\|")
        print(f"| {c['kind']} | `{command}` | {'yes' if c['in_ci'] else 'no'} | {'yes' if c['writes'] else 'no'} "
              f"| {c['source']} | {note.replace('|', chr(92) + '|')[:120]} |")
    if not checks:
        print("| — | no checks found | | | | |")
    print("\n## Commit hooks (run on every commit; agents trigger them too)\n")
    for h in result["hooks"]:
        print(f"- {h['hook']}: `{h['command']}`{'  ← rewrites files' if h['writes'] else ''} ({h['source']})")
    if not result["hooks"]:
        print("None found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
