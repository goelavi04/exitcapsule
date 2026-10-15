"""Stage 2b — AST usage scanning.

Why: "where and how is each dependency used" must be answered from evidence,
not guessed. Every import and every symbol reached through it (calls,
attributes, decorators, base classes, inner `Config` classes, keyword args)
is recorded with file:line and a snippet. Private API use is flagged here.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from engine.fsutil import iter_files, read_text, rel
from engine.models import UsageSite

STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}

# Stdlib modules removed in 3.12 / 3.13 (PEP 594, PEP 632 and friends). Importing
# one of these is an observed Python-compatibility risk, not a third-party dep.
REMOVED_STDLIB: dict[str, str] = {
    **{m: "3.12" for m in ("distutils", "imp", "asynchat", "asyncore", "smtpd")},
    **{
        m: "3.13"
        for m in (
            "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap",
            "msilib", "nis", "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd", "sunau",
            "telnetlib", "uu", "xdrlib", "lib2to3",
        )
    },
}  # fmt: skip


def is_test_path(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return (
        any(p in ("tests", "test", "testing") for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def local_modules(root: Path) -> set[str]:
    """Top-level names that resolve to the repo's own code (packages, modules, scripts)."""
    names: set[str] = set()
    for p in iter_files(root, (".py", ".pyi")):
        parts = list(p.relative_to(root).parts)
        if parts[0] in ("src", "lib") and len(parts) > 1:
            parts = parts[1:]
        # top-level package or module
        names.add(Path(parts[0]).stem if len(parts) == 1 else parts[0])
        # script-style directories (no __init__.py) import their siblings directly
        if not (p.parent / "__init__.py").exists():
            names.add(p.stem)
    names.discard("__init__")
    return names


@dataclass
class FileUsage:
    sites: list[UsageSite] = field(default_factory=list)
    imported: dict[str, int] = field(default_factory=dict)  # top-level module -> first line
    removed_stdlib: dict[str, int] = field(default_factory=dict)
    guarded: set[str] = field(default_factory=set)  # imported inside try/except ImportError
    parse_error: str | None = None


class _Visitor(ast.NodeVisitor):
    def __init__(self, file: str, lines: list[str], local: set[str], in_tests: bool) -> None:
        self.file = file
        self.lines = lines
        self.local = local
        self.in_tests = in_tests
        self.aliases: dict[str, str] = {}  # local name -> dotted qualname
        self.out = FileUsage()
        self._try_import_depth = 0
        self._class_bases_third_party: list[bool] = []

    # ------------------------------------------------------------------ helpers
    def _third_party(self, top: str) -> bool:
        """True for modules we track: third-party ones and removed stdlib modules."""
        if not top or top in self.local:
            return False
        return top in REMOVED_STDLIB or top not in STDLIB

    def _snippet(self, line: int) -> str:
        return self.lines[line - 1].strip()[:200] if 0 < line <= len(self.lines) else ""

    def _add(self, node: ast.AST, qualname: str, kind: str) -> None:
        top = qualname.split(".")[0]
        line = getattr(node, "lineno", 0)
        private = any(_is_private(p) for p in qualname.split(".")[1:])
        self.out.sites.append(
            UsageSite(
                file=self.file,
                line=line,
                col=getattr(node, "col_offset", 0),
                module=top,
                qualname=qualname,
                kind=kind,
                snippet=self._snippet(line),
                private=private,
                in_tests=self.in_tests,
            )
        )

    def resolve(self, node: ast.AST) -> str | None:
        """Dotted qualname for a Name/Attribute chain rooted at a third-party alias."""
        attrs: list[str] = []
        while isinstance(node, ast.Attribute):
            attrs.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name) and node.id in self.aliases:
            return ".".join([self.aliases[node.id], *reversed(attrs)])
        return None

    def _note_import(self, top: str, node: ast.AST) -> None:
        line = getattr(node, "lineno", 0)
        if top in self.local:
            return
        if top in REMOVED_STDLIB:
            self.out.removed_stdlib.setdefault(top, line)
            return
        if not self._third_party(top):
            return
        self.out.imported.setdefault(top, line)
        if self._try_import_depth:
            self.out.guarded.add(top)

    # ------------------------------------------------------------------ imports
    def visit_Try(self, node: ast.Try) -> None:
        catches_import = any(
            h.type is not None
            and any(
                isinstance(n, ast.Name) and n.id in ("ImportError", "ModuleNotFoundError")
                for n in ast.walk(h.type)
            )
            for h in node.handlers
        )
        if catches_import:
            self._try_import_depth += 1
            for s in node.body:
                self.visit(s)
            self._try_import_depth -= 1
            for h in node.handlers:
                self.visit(h)
            for s in node.orelse + node.finalbody:
                self.visit(s)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            top = a.name.split(".")[0]
            self._note_import(top, node)
            if not self._third_party(top):
                continue
            if a.asname:
                self.aliases[a.asname] = a.name
            else:
                self.aliases[top] = top
            self._add(node, a.name, "import")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module:
            return  # relative import -> local code
        top = node.module.split(".")[0]
        self._note_import(top, node)
        if not self._third_party(top):
            return
        for a in node.names:
            if a.name == "*":
                self._add(node, f"{node.module}.*", "import")
                continue
            q = f"{node.module}.{a.name}"
            self.aliases[a.asname or a.name] = q
            self._add(node, q, "import")

    # ------------------------------------------------------------------ uses
    def visit_Call(self, node: ast.Call) -> None:
        q = self.resolve(node.func)
        if q:
            self._add(node, q, "call")
            for kw in node.keywords:
                if kw.arg:
                    self._add(kw, f"{q}({kw.arg}=)", "kwarg")
        else:
            self.visit(node.func)
        for a in node.args:
            self.visit(a)
        for kw in node.keywords:
            self.visit(kw.value)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        q = self.resolve(node)
        if q:
            self._add(node, q, "attribute")
        else:
            self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.aliases:
            self._add(node, self.aliases[node.id], "name")

    def _decorators(self, decos: list[ast.expr]) -> None:
        for d in decos:
            target = d.func if isinstance(d, ast.Call) else d
            q = self.resolve(target)
            if q:
                self._add(d, q, "decorator")
                if isinstance(d, ast.Call):
                    for kw in d.keywords:
                        if kw.arg:
                            self._add(kw, f"{q}({kw.arg}=)", "kwarg")
                    for a in d.args:
                        self.visit(a)
                    for kw in d.keywords:
                        self.visit(kw.value)
            else:
                self.visit(d)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._decorators(node.decorator_list)
        for child in [node.args, *node.body]:
            self.visit(child)
        if node.returns:
            self.visit(node.returns)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._decorators(node.decorator_list)
        third_party_base = False
        for b in node.bases:
            target = b.value if isinstance(b, ast.Subscript) else b
            q = self.resolve(target)
            if q:
                self._add(b, q, "base_class")
                third_party_base = True
            else:
                self.visit(b)
        for kw in node.keywords:
            self.visit(kw.value)
        self._class_bases_third_party.append(third_party_base)
        for stmt in node.body:
            # Inner configuration classes (e.g. `class Config:` / `class Meta:`) of a class
            # deriving from a third-party base are framework API surface.
            if (
                isinstance(stmt, ast.ClassDef)
                and third_party_base
                and stmt.name in ("Config", "Meta")
            ):
                base_q = next(
                    (
                        self.resolve(b.value if isinstance(b, ast.Subscript) else b)
                        for b in node.bases
                        if self.resolve(b.value if isinstance(b, ast.Subscript) else b)
                    ),
                    "",
                )
                self._add(stmt, f"{base_q}.{stmt.name}", "config_class")
            self.visit(stmt)
        self._class_bases_third_party.pop()


def scan_file(path: Path, root: Path, local: set[str]) -> FileUsage:
    file = rel(path, root)
    text = read_text(path)
    try:
        tree = ast.parse(text, filename=file)
    except (SyntaxError, ValueError) as exc:
        fu = FileUsage()
        fu.parse_error = f"{file}: {exc}"
        # Fall back to a regex import scan so py2-only files still contribute evidence.
        for i, line in enumerate(text.splitlines(), start=1):
            m = re.match(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", line)
            if m:
                top = (m.group(1) or m.group(2)).split(".")[0]
                if top not in STDLIB and top not in local:
                    fu.imported.setdefault(top, i)
                    fu.sites.append(
                        UsageSite(
                            file=file,
                            line=i,
                            module=top,
                            qualname=m.group(1) or m.group(2),
                            kind="import",
                            snippet=line.strip()[:200],
                            in_tests=is_test_path(file),
                        )
                    )
        return fu
    v = _Visitor(file, text.splitlines(), local, is_test_path(file))
    v.visit(tree)
    return v.out


@dataclass
class UsageScan:
    sites: list[UsageSite] = field(default_factory=list)
    imported: dict[str, list[str]] = field(default_factory=dict)  # module -> ["file:line"]
    removed_stdlib: dict[str, list[str]] = field(default_factory=dict)
    guarded: set[str] = field(default_factory=set)
    parse_errors: list[str] = field(default_factory=list)
    local: set[str] = field(default_factory=set)
    py_files: int = 0
    test_files: int = 0


def scan_usage(root: Path) -> UsageScan:
    local = local_modules(root)
    us = UsageScan(local=local)
    for p in iter_files(root, (".py",)):
        us.py_files += 1
        file = rel(p, root)
        if is_test_path(file):
            us.test_files += 1
        fu = scan_file(p, root, local)
        us.sites += fu.sites
        for m, line in fu.imported.items():
            us.imported.setdefault(m, []).append(f"{file}:{line}")
        for m, line in fu.removed_stdlib.items():
            us.removed_stdlib.setdefault(m, []).append(f"{file}:{line}")
        us.guarded |= fu.guarded
        if fu.parse_error:
            us.parse_errors.append(fu.parse_error)
    return us
