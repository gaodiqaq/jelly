"""Distribution smoke tests for the bundled Jelly Studio frontend."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from agent_shell.server import app as app_module

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SMOKE_ENV = "JELLY_RUN_PACKAGE_SMOKE"


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, (
        f"command failed ({result.returncode}): {' '.join(command)}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _assert_wheel_has_webui(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "agent_shell/webui/index.html" in names
    assert "agent_shell/webui/favicon.svg" in names
    assert any(
        name.startswith("agent_shell/webui/assets/") and name.endswith(".js") for name in names
    )
    assert any(
        name.startswith("agent_shell/webui/assets/") and name.endswith(".css") for name in names
    )


def test_find_dist_dir_prefers_package_then_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "installed" / "agent_shell"
    packaged_ui = package_root / "webui"
    source_ui = tmp_path / "checkout" / "webui" / "dist"
    packaged_ui.mkdir(parents=True)
    source_ui.mkdir(parents=True)
    (packaged_ui / "index.html").write_text("packaged", encoding="utf-8")
    (source_ui / "index.html").write_text("source", encoding="utf-8")

    monkeypatch.setattr(app_module.resources, "files", lambda _package: package_root)
    monkeypatch.setattr(app_module, "_SOURCE_DIST_DIR", source_ui)

    assert app_module._find_dist_dir() == packaged_ui
    (packaged_ui / "index.html").unlink()
    assert app_module._find_dist_dir() == source_ui


@pytest.mark.skipif(
    os.environ.get(PACKAGE_SMOKE_ENV) != "1",
    reason=f"set {PACKAGE_SMOKE_ENV}=1 after npm run build to exercise distribution builds",
)
def test_sdist_builds_installable_wheel_with_webui(tmp_path: Path) -> None:
    """Build source artifacts, rebuild from sdist, install, and serve the bundled UI."""
    source_index = ROOT / "webui" / "dist" / "index.html"
    assert source_index.is_file(), "run `npm ci && npm run build` in webui before packaging"

    direct_dist = tmp_path / "direct-dist"
    direct_dist.mkdir()
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(direct_dist),
            str(ROOT),
        ],
        cwd=ROOT,
    )

    direct_wheel = next(direct_dist.glob("*.whl"))
    sdist = next(direct_dist.glob("*.tar.gz"))
    _assert_wheel_has_webui(direct_wheel)
    with tarfile.open(sdist, "r:gz") as archive:
        assert any(name.endswith("/webui/dist/index.html") for name in archive.getnames())

    extracted = tmp_path / "sdist-source"
    shutil.unpack_archive(str(sdist), extracted)
    source_dirs = [path for path in extracted.iterdir() if path.is_dir()]
    assert len(source_dirs) == 1

    rebuilt_dist = tmp_path / "rebuilt-dist"
    rebuilt_dist.mkdir()
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--outdir",
            str(rebuilt_dist),
            str(source_dirs[0]),
        ],
        cwd=tmp_path,
    )
    rebuilt_wheel = next(rebuilt_dist.glob("*.whl"))
    _assert_wheel_has_webui(rebuilt_wheel)

    site = tmp_path / "site"
    site.mkdir()
    with zipfile.ZipFile(rebuilt_wheel) as archive:
        archive.extractall(site)

    smoke = """
import os
import re
from pathlib import Path

import agent_shell
from fastapi.testclient import TestClient

from agent_shell.config import Settings
from agent_shell.runtime import ProviderStore
from agent_shell.server.app import create_app

site = Path(os.environ["JELLY_SMOKE_SITE"]).resolve()
work = Path.cwd()
assert Path(agent_shell.__file__).resolve().is_relative_to(site)
settings = Settings(cwd=work, session_dir=work / "sessions")
store = ProviderStore(work / "runtime.yaml")
with TestClient(create_app(settings, api_token=None, store=store)) as client:
    response = client.get("/")
    assert response.status_code == 200, response.text
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-cache"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert 'id="root"' in response.text
    script = re.search(r'<script[^>]+src="([^"]+\\.js)"', response.text)
    assert script, response.text
    asset = client.get(script.group(1))
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]
    assert len(asset.content) > 1000
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site)
    env["JELLY_SMOKE_SITE"] = str(site)
    _run([sys.executable, "-c", smoke], cwd=tmp_path, env=env)
