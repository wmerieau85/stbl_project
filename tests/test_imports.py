"""Chaque module s'importe : détecte un import cassé dans un module que les autres tests ne chargent pas."""

import importlib
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULES = sorted(".".join(p.relative_to(ROOT).with_suffix("").parts) for p in (ROOT / "scripts").rglob("*.py")) + ["main"]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)
