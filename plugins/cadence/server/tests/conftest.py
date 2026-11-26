from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import DATA, fake_env, install  # noqa: E402

from cadence_mcp.config import load_settings  # noqa: E402
from cadence_mcp.jobs import JobManager  # noqa: E402
from cadence_mcp.profiles import ProfileManager  # noqa: E402


@pytest.fixture()
def fakebin(tmp_path: Path) -> Path:
    return install(tmp_path / "bin")


@pytest.fixture()
def settings(tmp_path: Path, fakebin: Path, monkeypatch: pytest.MonkeyPatch):
    def make(access: str = "run", **extra: str):
        env = fake_env(fakebin)
        env.update({"CADENCE_MCP_WORKDIR": str(tmp_path / "work"), "CADENCE_MCP_ACCESS": access,
                    "CADENCE_MCP_ALLOWED_ROOTS": f"{tmp_path}:{DATA}"})
        env.update(extra)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return load_settings(environ=env)
    return make


@pytest.fixture()
def managers(settings):
    s = settings()
    return s, JobManager(), ProfileManager(s)
