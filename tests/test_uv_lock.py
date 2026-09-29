"""uv.lock — с публичного PyPI: uv sync качает строго по адресам lock-файла, и с адресами зеркала
посторонний не поставит окружение. Переписать их может UV_DEFAULT_INDEX (README «Разработка»)."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPI = "https://pypi.org/simple"
FILES = "https://files.pythonhosted.org/"
FIX = "адреса не PyPI: uv lock без UV_DEFAULT_INDEX и закоммитить"


def test_pyproject_names_pypi_as_default_index() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    indexes = pyproject["tool"]["uv"]["index"]
    assert [(i["url"], i.get("default")) for i in indexes] == [(PYPI, True)]


def test_uv_lock_points_to_public_pypi() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    registries = {p["source"]["registry"] for p in lock["package"] if "registry" in p["source"]}
    assert registries == {PYPI}, FIX
    files = [
        f["url"]
        for p in lock["package"]
        for f in [p.get("sdist", {}), *p.get("wheels", [])]
        if "url" in f
    ]
    assert len(files) > 700
    assert [url for url in files if not url.startswith(FILES)] == [], FIX
