"""Coerenza tra le cartelle di `packages/*/src/` e i `pyproject.toml`.

Ogni pacchetto del workspace ha il codice direttamente in `src/`, mappato
sul nome di import via `[tool.setuptools] package-dir`: i sottopacchetti
vanno elencati a mano in `packages`. In sviluppo un sottopacchetto
dimenticato funziona lo stesso (l'install editable mappa solo il pacchetto
di primo livello su `src/`), ma manca dalla wheel installata
nell'immagine — un errore che si vedrebbe solo a runtime nel container.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_PACKAGES_DIR = Path(__file__).resolve().parents[2] / "packages"
_PACKAGE_DIRS = sorted(p for p in _PACKAGES_DIR.iterdir() if (p / "pyproject.toml").exists())


def _discovered_packages(package_dir: Path) -> set[str]:
    name = package_dir.name
    src = package_dir / "src"
    found = {name}
    for init in src.rglob("__init__.py"):
        if "__pycache__" in init.parts or init.parent == src:
            continue
        found.add(".".join([name, *init.parent.relative_to(src).parts]))
    return found


@pytest.mark.parametrize("package_dir", _PACKAGE_DIRS, ids=lambda p: p.name)
def test_ogni_sottopacchetto_e_elencato_nel_pyproject(package_dir: Path):
    config = tomllib.loads((package_dir / "pyproject.toml").read_text())
    setuptools = config["tool"]["setuptools"]

    assert setuptools["package-dir"] == {package_dir.name: "src"}
    assert set(setuptools["packages"]) == _discovered_packages(package_dir)
