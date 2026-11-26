#!/usr/bin/env python3
"""Map a codebase's dependency structure and flag code in the wrong place.

Reads Python (ast) and JavaScript/TypeScript (import/require/export-from) files,
resolves imports to files in the repo, groups files into packages, and reports:

  wrong-way    a lower layer imports a higher one (layers from --layers, or
               inferred from folder names such as api > services > models > utils)
  cycle        packages that import each other, directly or through others
  reach-in     an import of another package's internals (private names,
               internal/ folders, deep paths past the package's entry point)
  duplicate    the same top-level constant defined in more than one package
  hotspot      the files most of the codebase depends on

Every finding carries file:line. With --base REF, findings are limited to
files the change touches, and each import is marked new or existing by
comparing against REF.

Usage:
  structure.py [ROOT] [--path SUBDIR] [--depth N] [--layers "api>services>models"]
               [--base REF] [--json]

Standard library only. Reads files and git; changes nothing.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PY = {".py"}
JS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
SKIP = {".git", "node_modules", ".venv", "venv", "env", "dist", "build", "out", "coverage", "__pycache__",
        ".tox", ".next", "target", "vendor", "site-packages", ".mypy_cache", ".pytest_cache", "migrations"}
# Inferred layers, highest first: a folder may import its own layer or lower ones.
LAYER_WORDS = [
    ("entry", r"^(api|apis|routes?|routers?|handlers?|controllers?|views?|endpoints?|cli|commands?|pages?|app|ui|components?|web|server)$"),
    ("service", r"^(services?|domain|core|use_?cases?|features?|logic|managers?|workflows?|jobs?|tasks?)$"),
    ("data", r"^(models?|db|database|repositor(y|ies)|repos?|schemas?|entities|dal|stores?|persistence|orm|dto|types?)$"),
    ("base", r"^(utils?|lib|libs|common|shared|helpers?|config|constants?|infra|infrastructure)$"),
]
# Folders whose children are the real packages (monorepos, moduliths).
CONTAINERS = {"modules", "packages", "apps", "services", "libs", "plugins", "crates", "components"}
JS_IMPORT = re.compile(r"""(?:^|[\s;])(?:import\s[^'"]*?from\s*|import\s*|export\s[^'"]*?from\s*|require\(\s*|import\(\s*)['"]([^'"]+)['"]""")
CONST_PY = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=\s*(.+)$")
CONST_JS = re.compile(r"^export\s+const\s+([A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=\s*(.+?);?\s*$")


class Repo:
    def __init__(self, root: Path, scope: Path, depth: int):
        self.root, self.scope, self.depth = root, scope, depth
        self.files = [p for p in self._walk(scope)]
        self.file_set = {p.resolve() for p in self.files}
        self.aliases = self._ts_aliases()
        self.py_roots = [r for r in (root, root / "src", scope) if r.is_dir()]

    def _walk(self, base: Path):
        for path in sorted(base.rglob("*")):
            if any(part in SKIP or part.startswith(".") and part not in (".",) for part in path.relative_to(base).parts[:-1]):
                continue
            if path.is_file() and path.suffix in PY | JS and not path.name.endswith(".d.ts"):
                yield path

    def _ts_aliases(self) -> list[tuple[str, Path]]:
        aliases = []
        for name in ("tsconfig.json", "jsconfig.json"):
            path = self.root / name
            if not path.is_file():
                continue
            text = re.sub(r"//.*|/\*.*?\*/", "", path.read_text(errors="replace"), flags=re.S)
            text = re.sub(r",(\s*[}\]])", r"\1", text)
            try:
                options = json.loads(text).get("compilerOptions", {})
            except ValueError:
                continue
            base = self.root / options.get("baseUrl", ".")
            for pattern, targets in options.get("paths", {}).items():
                if targets:
                    aliases.append((pattern.rstrip("*"), base / targets[0].rstrip("*")))
        return sorted(aliases, key=lambda a: -len(a[0]))

    def package(self, path: Path) -> str:
        """The package a file belongs to: its first `depth` folders, where a container
        folder such as modules/ or packages/ counts its children as the packages."""
        parts = path.relative_to(self.scope).parts[:-1]
        if parts and parts[0] == "src" and self.scope == self.root:
            parts = parts[1:]
        depth = self.depth + 1 if parts and parts[0] in CONTAINERS and len(parts) > 1 else self.depth
        return "/".join(parts[:depth]) or "(root)"

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def resolve_js(self, src: Path, spec: str) -> Path | None:
        if spec.startswith("."):
            base = (src.parent / spec)
        else:
            base = None
            for prefix, target in self.aliases:
                if spec.startswith(prefix):
                    base = target / spec[len(prefix):]
                    break
            if base is None:
                return None
        for candidate in [base] + [base.with_name(base.name + ext) for ext in JS] + [base / f"index{ext}" for ext in JS]:
            if candidate.resolve() in self.file_set:
                return candidate.resolve()
        return None

    def resolve_py(self, src: Path, module: str, level: int) -> Path | None:
        if level:
            base = src.parent
            for _ in range(level - 1):
                base = base.parent
            roots = [base]
        else:
            roots = self.py_roots
        parts = [p for p in module.split(".") if p] if module else []
        for root in roots:
            for n in range(len(parts), 0, -1) if parts else [0]:
                stem = root.joinpath(*parts[:n]) if n else root
                for candidate in (stem.with_suffix(".py"), stem / "__init__.py"):
                    if candidate.resolve() in self.file_set:
                        return candidate.resolve()
        return None


def imports_of(repo: Repo, path: Path, text: str | None = None) -> list[dict]:
    """Imports of one file that resolve to files in the repo: target, line, names imported."""
    text = path.read_text(errors="replace") if text is None else text
    out = []
    if path.suffix in PY:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return out
        local_lines = {inner.lineno for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                       for inner in ast.walk(fn) if isinstance(inner, (ast.Import, ast.ImportFrom))}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = repo.resolve_py(path, node.module or "", node.level)
                if target:
                    out.append({"target": target, "line": node.lineno, "names": [a.name for a in node.names],
                                "spec": "." * node.level + (node.module or ""), "local": node.lineno in local_lines})
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = repo.resolve_py(path, alias.name, 0)
                    if target:
                        out.append({"target": target, "line": node.lineno, "names": [], "spec": alias.name,
                                    "local": node.lineno in local_lines})
    else:
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith(("//", "*")):
                continue
            for spec in JS_IMPORT.findall(line):
                target = repo.resolve_js(path, spec)
                if target:
                    names = re.findall(r"\b(\w+)\b", line.split("from")[0]) if "from" in line else []
                    out.append({"target": target, "line": number, "names": names, "spec": spec,
                                "local": "import(" in line})
    return out


def layer_of(package: str, layers: list[list[str]] | None) -> tuple[int, str] | None:
    names = package.split("/")
    if layers:
        for index, group in enumerate(layers):
            if any(n in group for n in names) or package in group:
                return index, "/".join(group)
        return None
    for name in reversed(names):
        for index, (label, pattern) in enumerate(LAYER_WORDS):
            if re.match(pattern, name, re.I):
                return index, label
    return None


def file_layer_of(path: Path, layers: list[list[str]]) -> tuple[int, str] | None:
    """Layer of a file inside its package, from its folder names or file name (router.py, user_service.py)."""
    names = [p.lower() for p in path.parts[:-1]]
    stem = path.stem.lower()
    for index, group in enumerate(layers):
        for word in group:
            word = word.lower()
            if word in names or stem == word or stem.endswith("_" + word) or stem.startswith(word + "_"):
                return index, "/".join(group)
    return None


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components with more than one package (Tarjan, iterative)."""
    index, low, on_stack, stack, result, counter = {}, {}, set(), [], [], [0]
    for start in list(graph):
        if start in index:
            continue
        work = [(start, iter(sorted(graph.get(start, ()))))]
        index[start] = low[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter[0]
                    counter[0] += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    result.append(sorted(component))
    return sorted(result, key=len, reverse=True)


def is_reach_in(repo: Repo, src: Path, imp: dict) -> str | None:
    target: Path = imp["target"]
    src_pkg, dst_pkg = repo.package(src), repo.package(target)
    if src_pkg == dst_pkg:
        return None
    private = [n for n in imp["names"] if n.startswith("_") and not n.startswith("__")]
    if private:
        return f"imports private name(s) {', '.join(private)}"
    rel_parts = target.relative_to(repo.scope).parts
    if any(p in ("internal", "_internal", "private", "impl") or (p.startswith("_") and not p.startswith("__")) for p in rel_parts[:-1]):
        return "imports from another package's internal folder"
    if target.suffix in PY and target.name.startswith("_") and target.name != "__init__.py":
        return f"imports private module {target.name}"
    pkg_depth = len(dst_pkg.split("/")) + (1 if repo.scope == repo.root and rel_parts and rel_parts[0] == "src" else 0)
    inside = rel_parts[pkg_depth:]
    if len(inside) >= 3 and target.stem not in ("index", "__init__"):
        return f"reaches {len(inside) - 1} folders deep into {dst_pkg}"
    return None


def constants(repo: Repo) -> dict[str, list[tuple[str, int, str, str]]]:
    found: dict[str, list[tuple[str, int, str, str]]] = defaultdict(list)
    for path in repo.files:
        pattern = CONST_PY if path.suffix in PY else CONST_JS
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            m = pattern.match(line)
            if m and re.match(r"""^(['"`].*['"`]|-?\d[\d_.]*|True|False|true|false)$""", m.group(2).strip().rstrip(";")):
                found[m.group(1)].append((repo.package(path), number, repo.rel(path), m.group(2).strip().rstrip(";")))
    return {name: hits for name, hits in found.items() if len({h[0] for h in hits}) > 1}


def cycle_detail(component: list[str], edge_examples: dict[tuple[str, str], list[str]]) -> dict:
    """Mutual pairs and the thinnest edges inside one cycle, with their import sites."""
    inside = {(a, b): sites for (a, b), sites in edge_examples.items() if a in component and b in component}
    mutual = []
    for (a, b), sites in inside.items():
        if a < b and (b, a) in inside:
            mutual.append({"a": a, "b": b, "ab": len(sites), "ba": len(inside[(b, a)]),
                           "example": sites[0] if len(sites) <= len(inside[(b, a)]) else inside[(b, a)][0]})
    mutual.sort(key=lambda p: min(p["ab"], p["ba"]))
    thinnest = sorted(({"edge": f"{a} → {b}", "sites": len(s), "examples": s[:2]} for (a, b), s in inside.items()),
                      key=lambda e: e["sites"])
    return {"packages": component, "edge_count": len(inside), "mutual_pairs": mutual,
            "thinnest_edges": [e for e in thinnest if e["sites"] <= 2]}


def changed_files(root: Path, base: str) -> set[Path]:
    run = lambda *args: subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout.split()
    names = set(run("diff", "--name-only", f"{base}...HEAD")) | set(run("diff", "--name-only")) | set(run("diff", "--name-only", "--cached"))
    return {(root / n).resolve() for n in names}


def base_text(root: Path, base: str, path: Path) -> str | None:
    rel_path = path.relative_to(root)
    result = subprocess.run(["git", "-C", str(root), "show", f"{base}:{rel_path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--path", help="only scan this subfolder (relative to ROOT)")
    parser.add_argument("--depth", type=int, default=1, help="folder levels that make a package (default 1; children of modules/, packages/, apps/ … count as packages)")
    parser.add_argument("--layers", help='layer order, highest first, e.g. "api>services>models>utils"; '
                                         'use | for peers: "routes|cli>services>db"')
    parser.add_argument("--base", help="git ref: only report what the change since REF touches")
    parser.add_argument("--exclude", default="", help="comma-separated folder names to skip at any depth, e.g. scripts,tests")
    parser.add_argument("--file-layers", help='layer order inside a package, matched on folder and file names, '
                                              'e.g. "router|routers>service|services>repository|repositories"')
    parser.add_argument("--limit", type=int, default=25, help="items per section in the text report; 0 prints all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    SKIP.update(n.strip() for n in args.exclude.split(",") if n.strip())
    root = Path(args.root).resolve()
    scope = (root / args.path).resolve() if args.path else root
    repo = Repo(root, scope, args.depth)
    layers = [group.split("|") for group in args.layers.split(">")] if args.layers else None
    file_layers = [group.split("|") for group in args.file_layers.split(">")] if args.file_layers else None
    file_imports: dict[Path, list[dict]] = {}
    file_base: dict[Path, set | None] = {}
    hidden, inner_wrong_way = [], []
    changed = changed_files(root, args.base) if args.base else None

    edges: dict[str, set[str]] = defaultdict(set)
    edge_examples: dict[tuple[str, str], list[str]] = defaultdict(list)
    fan_in: dict[Path, int] = defaultdict(int)
    wrong_way, reach_ins = [], []
    reach_targets: dict[str, dict] = defaultdict(lambda: {"packages": set(), "sites": [], "reason": ""})
    edge_new: dict[tuple[str, str], set[bool]] = defaultdict(set)  # change mode: is each site of an edge new?
    for path in repo.files:
        imps = imports_of(repo, path)
        base_targets = None
        if changed is not None and path.resolve() in changed:
            old = base_text(root, args.base, path)
            base_targets = {(i["target"], tuple(i["names"])) for i in imports_of(repo, path, old)} if old is not None else set()
        src_pkg = repo.package(path)
        file_imports[path.resolve()] = imps
        file_base[path.resolve()] = base_targets
        for imp in imps:
            dst_pkg = repo.package(imp["target"])
            fan_in[imp["target"]] += 1
            if src_pkg == dst_pkg:
                if file_layers and (changed is None or path.resolve() in changed):
                    a, b = file_layer_of(path.relative_to(root), file_layers), file_layer_of(imp["target"].relative_to(root), file_layers)
                    if a and b and b[0] < a[0]:
                        status = "" if base_targets is None else (
                            " (existing)" if (imp["target"], tuple(imp["names"])) in base_targets else " (NEW)")
                        inner_wrong_way.append(f"{repo.rel(path)}:{imp['line']}{status}: [{a[1]}] imports [{b[1]}] "
                                               f"{repo.rel(imp['target'])} inside {src_pkg}")
                continue
            edges[src_pkg].add(dst_pkg)
            where = f"{repo.rel(path)}:{imp['line']}"
            edge_examples[(src_pkg, dst_pkg)].append(where)
            is_new = base_targets is not None and (imp["target"], tuple(imp["names"])) not in base_targets
            edge_new[(src_pkg, dst_pkg)].add(is_new)
            if changed is not None and path.resolve() not in changed:
                continue
            status = "" if base_targets is None else (" (existing)" if (imp["target"], tuple(imp["names"])) in base_targets else " (NEW)")
            src_layer, dst_layer = layer_of(src_pkg, layers), layer_of(dst_pkg, layers)
            if src_layer and dst_layer and dst_layer[0] < src_layer[0]:
                wrong_way.append(f"{where}{status}: {src_pkg} [{src_layer[1]}] imports {dst_pkg} [{dst_layer[1]}] "
                                 f"via `{imp['spec']}`")
            reason = is_reach_in(repo, path, imp)
            if reason:
                reach_ins.append(f"{where}{status}: {src_pkg} {reason} (`{imp['spec']}` → {repo.rel(imp['target'])})")
                entry = reach_targets[repo.rel(imp["target"])]
                entry["packages"].add(src_pkg)
                entry["sites"].append(where + status)
                entry["reason"] = reason.split(" (")[0]

    for src, imps in file_imports.items():
        if changed is not None and src not in changed:
            continue
        for imp in imps:
            if not imp.get("local"):
                continue
            back = [b for b in file_imports.get(imp["target"], []) if b["target"] == src]
            if back:
                base = file_base.get(src)
                status = "" if base is None else (" (existing)" if (imp["target"], tuple(imp["names"])) in base else " (NEW)")
                hidden.append(f"{repo.rel(src)}:{imp['line']}{status} imports {repo.rel(imp['target'])} inside a function, and "
                              f"{repo.rel(imp['target'])}:{back[0]['line']} imports it back"
                              f"{' (also inside a function)' if back[0].get('local') else ''}: a cycle hidden at runtime")

    cycles = find_cycles(edges)
    new_edges = {e for e, flags in edge_new.items() if flags == {True}} if changed is not None else set()
    cycle_news: list[dict] = []
    if changed is not None:
        touched = {repo.package(p) for p in changed if p in repo.file_set}
        cycles = [c for c in cycles if touched & set(c)]
        old_graph: dict[str, set[str]] = defaultdict(set)
        for a, targets in edges.items():
            for b in targets:
                if (a, b) not in new_edges:
                    old_graph[a].add(b)
        old_sets = [set(c) for c in find_cycles(old_graph)]
        for c in cycles:
            added = [f"{a} → {b}: {', '.join(edge_examples[(a, b)][:2])}" for (a, b) in sorted(new_edges)
                     if a in c and b in c]
            created = not any(set(c) <= old for old in old_sets)
            cycle_news.append({"new_cycle": created, "new_edges": added})
    dupes = constants(repo)
    dupe_new: set[tuple[str, str]] = set()
    if changed is not None:
        changed_rel = {repo.rel(p) for p in changed if p in repo.file_set}
        dupes = {n: h for n, h in dupes.items() if any(x[2] in changed_rel for x in h)}
        for name, hits in dupes.items():
            for _, _, rel_path, _ in hits:
                if rel_path in changed_rel:
                    old = base_text(root, args.base, root / rel_path) or ""
                    if not re.search(rf"(?m)^(export\s+const\s+)?{re.escape(name)}\b", old):
                        dupe_new.add((name, rel_path))
    hotspots = sorted(fan_in.items(), key=lambda kv: -kv[1])[:8]
    packages = sorted({repo.package(p) for p in repo.files})
    layer_map = {p: layer_of(p, layers) for p in packages}

    result = {
        "root": str(root), "scope": repo.rel(scope) if scope != root else ".", "files": len(repo.files),
        "packages": len(packages), "base": args.base,
        "layers": "given" if layers else "inferred from folder names",
        "layer_map": {p: l[1] for p, l in layer_map.items() if l},
        "wrong_way": wrong_way,
        "cycles": [dict(cycle_detail(c, edge_examples), **(cycle_news[i] if cycle_news else {}))
                   for i, c in enumerate(cycles[:10])],
        "reach_ins": reach_ins,
        "reach_in_targets": [
            {"target": t, "packages": sorted(v["packages"]), "sites": len(v["sites"]), "examples": v["sites"][:3], "reason": v["reason"]}
            for t, v in sorted(reach_targets.items(), key=lambda kv: (-len(kv[1]["packages"]), -len(kv[1]["sites"])))
        ],
        "duplicates": {n: [f"{h[2]}:{h[1]}{' (NEW)' if (n, h[2]) in dupe_new else ''} = {h[3][:60]}" for h in hits]
                       for n, hits in sorted(dupes.items())},
        "hidden_cycles": hidden,
        "inner_wrong_way": inner_wrong_way,
        "hotspots": [f"{repo.rel(p)} ({n} importers)" for p, n in hotspots],
    }
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
        return 0

    print(f"# Structure of {root.name}{'/' + result['scope'] if result['scope'] != '.' else ''}\n")
    print(f"- {result['files']} files in {result['packages']} packages (package = first {args.depth} folder level(s))")
    print(f"- Layers: {result['layers']}: " + (", ".join(f"{p}={l}" for p, l in result["layer_map"].items()) or "none matched"))
    if args.base:
        print(f"- Change mode: only files changed since {args.base}; imports marked NEW or existing")

    def section(title: str, items: list[str], limit: int = args.limit or 10**9) -> None:
        print(f"\n## {title} ({len(items)})\n")
        print("\n".join(f"- {i}" for i in items[:limit]) if items else "None found.")
        if len(items) > limit:
            print(f"- … {len(items) - limit} more (--limit 0 prints all)")

    section("Wrong-way dependencies", wrong_way)
    if file_layers:
        section("Wrong-way imports inside packages", inner_wrong_way)
    section("Cycles hidden in function-level imports", hidden)
    print(f"\n## Package cycles ({len(cycles)})\n")
    if not cycles:
        print("None found.")
    for c in result["cycles"]:
        print(f"- {len(c['packages'])} packages: {', '.join(c['packages'])} ({c['edge_count']} import edges between them)")
        if args.base:
            verdict = "NEW: this change creates the cycle" if c.get("new_cycle") else "existing before this change"
            print(f"  - {verdict}; edges this change adds inside it: {len(c.get('new_edges', []))}")
            for edge in c.get("new_edges", [])[:8]:
                print(f"    - NEW edge {edge}")
        if c["mutual_pairs"]:
            print("  - Import each other directly (sites each way):")
            for pair in c["mutual_pairs"][:10]:
                print(f"    - {pair['a']} → {pair['b']}: {pair['ab']} · {pair['b']} → {pair['a']}: {pair['ba']}  "
                      f"(e.g. {pair['example']})")
        if c["thinnest_edges"]:
            print("  - Thinnest edges, the cheapest places to cut:")
            for edge in c["thinnest_edges"][:8]:
                print(f"    - {edge['edge']}: {edge['sites']} site(s): {', '.join(edge['examples'])}")
    print(f"\n## Reach-ins ({len(reach_ins)} sites into {len(result['reach_in_targets'])} files)\n")
    if not reach_ins:
        print("None found.")
    reach_limit = args.limit or 10**9
    for t in result["reach_in_targets"][:reach_limit]:
        print(f"- {t['target']}: {t['reason']}; imported from {len(t['packages'])} package(s) "
              f"({', '.join(t['packages'][:5])}), {t['sites']} site(s), e.g. {t['examples'][0]}")
    if len(result["reach_in_targets"]) > reach_limit:
        print(f"- … {len(result['reach_in_targets']) - reach_limit} more files (--limit 0 prints all)")
    section("Duplicated constants", [f"`{n}`: " + "; ".join(v) for n, v in result["duplicates"].items()])
    section("Hotspots (most depended-on files)", result["hotspots"], 8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
