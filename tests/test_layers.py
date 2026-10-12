"""Package layers: menu.db, menu.ingest and menu.nutrition import
downward only.

domain (pure rules and types, imports nothing from menu outside
menu.domain) -> db -> ingest -> nutrition -> scripts. This walks every
module with ast — including TYPE_CHECKING and in-function imports —
and fails when a module imports a strictly higher layer, so the
package cycle the architecture review found (finding 1) cannot creep
back.
"""

import ast
from pathlib import Path

MENU_DIR = Path(__file__).resolve().parent.parent / "menu"

# Lower never imports higher.
LAYER_OF = {"domain": 0, "db": 1, "ingest": 2, "nutrition": 3, "scripts": 4}


def _imported_menu_modules(tree: ast.Module) -> list[tuple[int, str]]:
    """(line, module) for every menu.* import, wherever it appears."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "menu" or alias.name.startswith("menu."):
                    found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and (
            node.module == "menu"
            or (node.module is not None and node.module.startswith("menu."))
        ):
            found.append((node.lineno, node.module))
    return found


def test_package_layers_import_downward_only() -> None:
    violations: list[str] = []
    for path in sorted(MENU_DIR.rglob("*.py")):
        module_path = path.relative_to(MENU_DIR.parent).as_posix()
        parts = path.relative_to(MENU_DIR).parts
        if len(parts) == 1:  # menu/__init__.py is exempt
            continue
        layer = LAYER_OF.get(parts[0])
        if layer is None:  # not one of the mapped layers
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, target in _imported_menu_modules(tree):
            target_parts = target.split(".")
            target_layer = (
                LAYER_OF.get(target_parts[1]) if len(target_parts) > 1 else None
            )
            if target_layer is None:
                continue
            if target_layer > layer or (layer == 0 and target_layer != 0):
                violations.append(f"{module_path}:{lineno} imports {target}")
    assert not violations, (
        "The package layers domain -> db -> ingest -> nutrition -> "
        "scripts are one-way: no module may import a strictly higher "
        "layer, and menu.domain imports nothing outside menu.domain. "
        "Violations:\n  " + "\n  ".join(violations)
    )
