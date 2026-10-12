"""Session boundary: only entrypoints and jobs open a session.

Library functions take `session: Session` from the caller — that is
what lets them join the caller's transaction and keeps their tests off
the global engine. This walks the package with ast and fails when a
`get_session` / `get_ro_session` call appears outside the allowlisted
entrypoints and jobs.
"""

import ast
from pathlib import Path

MENU_DIR = Path(__file__).resolve().parent.parent / "menu"

# The entrypoints and jobs that may open their own session: the CLI
# and crawl pipeline, the rebuild job, the reference importers, the
# eval scripts, and connection.py, which defines the factories. The
# whole scripts layer is entrypoints and top-level jobs by definition.
ALLOWED = {
    "menu/db/connection.py",
    "menu/ingest/embeddings_eval.py",
    "menu/ingest/llm_eval.py",
    "menu/ingest/pipeline.py",
    "menu/nutrition/sources/cnf.py",
    "menu/nutrition/sources/cofid.py",
    "menu/nutrition/sources/fdc.py",
}
ALLOWED_PACKAGE = "menu/scripts/"

SESSION_FACTORIES = {"get_session", "get_ro_session"}


def _called_name(call: ast.Call) -> str | None:
    """The name of the function a Call invokes, however it was
    reached (get_session(...) or connection.get_session(...))."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def test_library_code_never_opens_its_own_session() -> None:
    offenders: list[str] = []
    for path in sorted(MENU_DIR.rglob("*.py")):
        module = path.relative_to(MENU_DIR.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _called_name(node)
            if (
                name in SESSION_FACTORIES
                and module not in ALLOWED
                and not module.startswith(ALLOWED_PACKAGE)
            ):
                offenders.append(f"{module}:{node.lineno}")
    assert not offenders, (
        "Library functions must take `session: Session` from the caller; "
        "only entrypoints and jobs open their own session. These files "
        "call get_session/get_ro_session:\n  " + "\n  ".join(offenders)
    )
