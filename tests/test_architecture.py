"""The layering contract, enforced instead of documented.

The dependency direction is one-way: core -> models -> services -> api. Nothing
stops a future edit from importing upward except this test, and an upward
import quietly turns the layers back into one tangled package.
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

# layer -> the layers it must never import from
FORBIDDEN = {
    "core": ("models", "services", "api"),
    "models": ("services", "api"),
    "services": ("api",),
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _python_files(layer: str) -> list[Path]:
    return sorted((APP / layer).rglob("*.py"))


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_upward(layer):
    banned = FORBIDDEN[layer]
    offences = []
    for path in _python_files(layer):
        for module in _imported_modules(path):
            for upper in banned:
                if module == f"app.{upper}" or module.startswith(f"app.{upper}."):
                    offences.append(f"{path.relative_to(APP.parent)} -> {module}")
    assert not offences, "upward imports break the layering: " + ", ".join(offences)


def test_layers_exist():
    for layer in ("core", "models", "services", "api"):
        assert (APP / layer / "__init__.py").is_file(), f"app/{layer} is not a package"


def test_no_sqlalchemy_or_sqlmodel_anywhere():
    """The DB layer is Peewee; a stray SQLAlchemy import would mean two ORMs."""
    offences = []
    for path in APP.rglob("*.py"):
        for module in _imported_modules(path):
            root = module.split(".")[0]
            if root in {"sqlalchemy", "sqlmodel"}:
                offences.append(f"{path.relative_to(APP.parent)} -> {module}")
    assert not offences, "unexpected ORM import: " + ", ".join(offences)


def test_api_layer_holds_no_model_queries():
    """Routers translate HTTP to service calls; queries belong in services.

    Catching `Model.select(...)` in a router is the cheap proxy for that rule.
    """
    offences = []
    query_methods = {"select", "insert", "update", "delete", "get_or_none", "get_by_id"}
    for path in _python_files("api"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in query_methods
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id[:1].isupper()
            ):
                offences.append(
                    f"{path.relative_to(APP.parent)}:{node.lineno} "
                    f"{node.func.value.id}.{node.func.attr}()"
                )
    assert not offences, "queries in the API layer: " + ", ".join(offences)
