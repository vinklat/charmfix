"""Imports between charmfix modules point downwards → DEVELOPMENT.md#dependencies."""

from __future__ import annotations

import ast

import pytest
from test_docs import ROOT, code_spans, section, table

PACKAGE = ROOT / "src" / "charmfix"
MODULES = sorted(p.stem for p in PACKAGE.glob("*.py"))

# Module → layer is checked against DEVELOPMENT.md; the allowed edges below restate its
# "May import" column, which is prose.
LAYERS = {
    "Entry": {"cli"},
    "Orchestration": {"pipeline"},
    "Steps": {"repair", "exif_write", "verify"},
    "I/O": {"discovery", "output", "report"},
    "Primitives": {"jpeg", "exif_read", "dates"},
}
STEPS, IO, PRIMITIVES = LAYERS["Steps"], LAYERS["I/O"], LAYERS["Primitives"]
# "__init__" stands for the package itself, i.e. `from . import __version__`.
MAY_IMPORT = {
    "cli": {"__init__", "pipeline"} | STEPS | IO | PRIMITIVES,
    "pipeline": STEPS | IO | PRIMITIVES,
    "repair": PRIMITIVES,
    "exif_write": {"repair"} | PRIMITIVES,
    "verify": {"repair"} | PRIMITIVES,
    **{m: {"__init__"} for m in IO},
    **{m: set() for m in PRIMITIVES},
    "__init__": set(),
}


def imports_in(source: str) -> set[str]:
    """The charmfix modules (or ``__init__``) that ``source`` imports, relative or absolute."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:
                name = node.module
            elif node.module and node.module.split(".")[0] == "charmfix":
                name = node.module.partition(".")[2] or None
            else:
                continue
            if name:
                found.add(name.split(".")[0])
            else:  # `from . import x`: a module, or a package-level name
                found |= {a.name if a.name in MODULES else "__init__" for a in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top, _, rest = alias.name.partition(".")
                if top == "charmfix":
                    found.add(rest.split(".")[0] if rest else "__init__")
    return found


def internal_imports(module: str) -> set[str]:
    return imports_in((PACKAGE / f"{module}.py").read_text())


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from .repair import RepairPlan", {"repair"}),
        ("from . import jpeg", {"jpeg"}),
        ("from . import __version__", {"__init__"}),
        ("from charmfix.repair import RepairPlan", {"repair"}),
        ("from charmfix import jpeg", {"jpeg"}),
        ("import charmfix.repair", {"repair"}),
        ("import charmfix", {"__init__"}),
        ("import os\nfrom pathlib import Path", set()),
    ],
)
def test_imports_are_found(source: str, expected: set[str]) -> None:
    assert imports_in(source) == expected


def test_real_imports_are_found() -> None:
    assert {"repair", "jpeg", "exif_read"} <= internal_imports("verify")


def test_layers_match_development_md() -> None:
    rows = table(section(ROOT / "DEVELOPMENT.md", "Dependencies"), "Layer")
    assert {row[0]: set(code_spans(row[1])) for row in rows} == LAYERS


def test_every_module_has_a_layer() -> None:
    assert set(MODULES) == set(MAY_IMPORT)


@pytest.mark.parametrize("module", MODULES)
def test_imports_point_downwards(module: str) -> None:
    assert internal_imports(module) <= MAY_IMPORT[module]
