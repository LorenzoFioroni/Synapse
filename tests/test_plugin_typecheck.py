"""The plugin type checker must resolve host types without executing or shadowing plugins."""

from tools.typecheck_plugins import check_plugin


def test_reused_packages_keep_relative_imports_and_host_types(tmp_path, capsys):
    folder = tmp_path / "example-plugin"
    backend = folder / "backend"
    server = folder / "server"
    backend.mkdir(parents=True)
    server.mkdir()
    (backend / "__init__.py").write_text(
        "from backend.plugins import LibraryReader\n"
        "from .helper import read\n"
        "from ..server import greeting\n"
        'raise RuntimeError("The checker must never execute this plugin")\n'
    )
    (backend / "helper.py").write_text(
        "from backend.plugins import LibraryReader\n"
        "def read(library: LibraryReader) -> dict | None:\n"
        '    return library.get("paper")\n'
    )
    (server / "__init__.py").write_text('def greeting() -> str:\n    return "hello"\n')
    before = {p: p.read_bytes() for p in folder.rglob("*.py")}
    assert check_plugin(folder) == 0
    assert "Success" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in folder.rglob("*.py")} == before


def test_api_errors_and_unimported_untyped_bodies_fail(tmp_path, capsys):
    folder = tmp_path / "broken-plugin"
    backend = folder / "backend"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text(
        "from backend.plugins import LibraryReader\ndef read(library: LibraryReader):\n    return library.get(123)\n"
    )
    (backend / "unused.py").write_text('def untyped():\n    count: int = "wrong"\n    return count\n')
    assert check_plugin(folder) != 0
    output = capsys.readouterr().out
    assert "[arg-type]" in output
    assert "[assignment]" in output
    assert "broken-plugin/backend/unused.py" in output
    assert "synapse-plugin-mypy-" not in output
