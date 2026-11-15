import socket
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*a, **k):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


@pytest.fixture(autouse=True)
def _no_claude_cli(monkeypatch):
    """测试里不许真的调用本机 claude 命令；需要 CLI 后端的测试自己注入假的 runner。"""
    monkeypatch.setattr("aivc.llm.cli_available", lambda command: False)


@pytest.fixture
def config_dir() -> Path:
    return Path(__file__).parent.parent / "config"


@pytest.fixture(autouse=True)
def _site_dir_tmp(monkeypatch, tmp_path):
    """update/weekly 会自动建站；把默认输出目录指到临时目录，避免写进项目的 ./site。"""
    monkeypatch.setenv("AIVC_SITE_DIR", str(tmp_path / "site"))


@pytest.fixture(autouse=True)
def _local_profile_tmp(monkeypatch, tmp_path):
    """默认不启用 --local，且私有目录指到临时目录：测试不会写进项目的 ./local，也不受本机 .env 影响。"""
    monkeypatch.setenv("AIVC_LOCAL", "0")
    monkeypatch.setenv("AIVC_LOCAL_DIR", str(tmp_path / "local"))
