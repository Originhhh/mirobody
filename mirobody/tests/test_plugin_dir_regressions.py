"""Plugin directories keep their package wherever they are configured.

`utils/plugin_dirs.resolve_plugin_dir` turns an `MCP_TOOL_DIRS`, `AGENT_DIRS`
or `TASK_DIRS` entry into a directory and a dotted prefix for all three
loaders. Each case builds a toy package whose module uses a relative import,
the thing a package directory loses when it is loaded file by file (4 of the
9 built-in tools on 1.5.7). Library-only: stdlib and `tmp_path`.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import uuid
from pathlib import Path

import pytest

from mirobody.utils.plugin_dirs import import_plugin_module, resolve_plugin_dir


@pytest.fixture
def package():
    name = f"plugin_pkg_{uuid.uuid4().hex[:8]}"
    yield name
    for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[key]


def make_package(root: Path, name: str) -> Path:
    tools = root / name / "tools"
    tools.mkdir(parents=True)
    (root / name / "__init__.py").write_text("")
    (tools / "__init__.py").write_text("")
    (tools / "_helper.py").write_text("VALUE = 1\n")
    (tools / "lab.py").write_text("from ._helper import VALUE\n")
    return tools


def run_from(monkeypatch, cwd: Path, *roots: Path) -> None:
    """CWD off sys.path, as under the `mirobody serve` console script."""
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "path", [str(r) for r in roots] + [p for p in sys.path if p not in ("", os.getcwd())])
    importlib.invalidate_caches()


def assert_loads(directory: str, prefix: str | None, copy: Path) -> None:
    _, module = import_plugin_module(directory, prefix, "lab.py")
    assert module.VALUE == 1
    assert os.path.realpath(module.__file__) == os.path.realpath(copy / "lab.py")


@pytest.mark.parametrize("spelling", ["absolute", "trailing slash", "dotdot", "symlink"])
def test_a_package_directory_keeps_its_name_from_any_working_directory(tmp_path, monkeypatch, package, spelling):
    tools = make_package(tmp_path / "checkout", package)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    configured = {
        "absolute": str(tools),
        "trailing slash": str(tools) + os.sep,
        "dotdot": os.path.join(os.pardir, "checkout", package, "tools"),
        "symlink": str(tmp_path / "link"),
    }[spelling]
    if spelling == "symlink":
        os.symlink(tools, configured)
    run_from(monkeypatch, elsewhere, tmp_path / "checkout")

    directory, prefix = resolve_plugin_dir(configured)

    assert prefix == f"{package}.tools"
    assert os.path.realpath(directory) == os.path.realpath(tools)
    assert_loads(directory, prefix, tools)


@pytest.mark.parametrize("spelling", ["relative", "absolute below the working directory"])
def test_a_checkout_beside_an_installed_copy_loads_the_installed_package(tmp_path, monkeypatch, caplog, package, spelling):
    """A checkout with a non-editable install, served from the checkout root.

    The checkout copy cannot be imported as its package here, and loading it by
    file drops every module with a relative import. The dotted spelling reaches
    the installed copy, as on 1.5.7, and a warning names both locations.
    """
    installed = make_package(tmp_path / "site", package)
    checkout = make_package(tmp_path / "checkout", package)
    run_from(monkeypatch, tmp_path / "checkout", tmp_path / "site")
    configured = f"{package}/tools" if spelling == "relative" else str(checkout)

    with caplog.at_level(logging.WARNING, logger="mirobody.utils.plugin_dirs"):
        directory, prefix = resolve_plugin_dir(configured)

    assert prefix == f"{package}.tools"
    assert os.path.realpath(directory) == os.path.realpath(installed)
    assert_loads(directory, prefix, installed)
    (warning,) = caplog.messages
    assert "loading that copy" in warning
    assert f"directory={os.path.abspath(checkout)}" in warning
    assert f"imported_from={directory}" in warning


def test_an_explicit_path_is_never_swapped_for_the_installed_copy(tmp_path, monkeypatch, caplog, package):
    """A spelling that names no package keeps its own files, and says why they may fail."""
    make_package(tmp_path / "site", package)
    checkout = make_package(tmp_path / "checkout", package)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    run_from(monkeypatch, elsewhere, tmp_path / "site")

    with caplog.at_level(logging.WARNING, logger="mirobody.utils.plugin_dirs"):
        directory, prefix = resolve_plugin_dir(str(checkout))

    assert (directory, prefix) == (str(checkout), None)
    (warning,) = caplog.messages
    assert "loading its files by location" in warning
    assert f"directory={checkout}" in warning
    assert f"imported_from={tmp_path / 'site' / package / 'tools'}" in warning


def test_a_directory_that_imports_as_itself_wins_over_an_installed_namesake(tmp_path, monkeypatch, caplog, package):
    make_package(tmp_path / "site", package)
    checkout = make_package(tmp_path / "checkout", package)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    run_from(monkeypatch, elsewhere, tmp_path / "checkout", tmp_path / "site")

    with caplog.at_level(logging.WARNING, logger="mirobody.utils.plugin_dirs"):
        directory, prefix = resolve_plugin_dir(str(checkout))

    assert (directory, prefix) == (str(checkout), f"{package}.tools")
    assert_loads(directory, prefix, checkout)
    assert not caplog.messages


def test_a_plain_directory_is_never_replaced_by_an_installed_namesake(tmp_path, monkeypatch, caplog, package):
    make_package(tmp_path / "site", package)
    plain = tmp_path / "checkout" / package / "tools"
    plain.mkdir(parents=True)
    run_from(monkeypatch, tmp_path / "checkout", tmp_path / "site")

    with caplog.at_level(logging.WARNING, logger="mirobody.utils.plugin_dirs"):
        directory, prefix = resolve_plugin_dir(f"{package}/tools")

    assert (directory, prefix) == (str(plain), None)
    assert not caplog.messages
