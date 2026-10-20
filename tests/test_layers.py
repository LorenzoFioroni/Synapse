"""The backend packages depend on each other in one direction only (docs/architecture.md, "Layers").

A new import that breaks the layering fails here: either the code belongs in another package, or
the layering in docs/architecture.md and ALLOWED below change together, on purpose.
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"

# What each package (or top-level module) of backend/ may import from the others.
BASE = {"config", "settings", "changes"}
ALLOWED = {
    "config": set(),
    "settings": set(),
    "changes": set(),
    "library": BASE,
    "formats": BASE | {"library"},
    "notes": BASE | {"library"},
    "pdf": BASE | {"library"},
    "tasks": BASE | {"library", "notes"},
    "online": BASE | {"library", "formats", "pdf"},
    "references": BASE | {"library", "formats", "online"},
    "mcp": BASE | {"library", "notes", "pdf", "references", "tasks"},
    "backup": BASE,
    "access": BASE,
    "assist": BASE,
    # the composition layer: wires and exposes everything
    "services": None,
    "connector": None,
    "api": None,
    "frontend": None,
    "app": None,
}


def unit(rel: tuple[str, ...]) -> str:
    """The package or top-level module a path inside backend/ belongs to."""
    return rel[0].removesuffix(".py")


def imports_of(path: Path):
    rel = path.relative_to(BACKEND).with_suffix("").parts
    pkg = rel[:-1]  # the package a module (or a package's __init__) is in
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = list(pkg[: len(pkg) - (node.level - 1)])
            if node.module:
                yield tuple(base + node.module.split("."))
            else:
                for a in node.names:
                    yield tuple(base + [a.name])
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend."):
            yield tuple(node.module.split(".")[1:])


def test_every_unit_is_listed():
    units = {unit(p.relative_to(BACKEND).parts) for p in BACKEND.glob("*") if p.suffix == ".py" or p.is_dir()}
    units -= {"__init__", "__pycache__"}
    assert units == set(ALLOWED), "add the new package to ALLOWED (and to docs/architecture.md)"


def test_imports_follow_the_layers():
    wrong = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).parts
        if rel == ("__init__.py",):
            continue
        src = unit(rel)
        allowed = ALLOWED[src]
        if allowed is None:
            continue
        for target in imports_of(path):
            if not target:
                continue
            dst = unit(target)
            if dst != src and dst not in allowed:
                wrong.append(f"{path.relative_to(BACKEND.parent)} imports backend.{'.'.join(target)}")
    assert not wrong, "imports against the layering:\n" + "\n".join(wrong)
