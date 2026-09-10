"""测试仅使用合成数据与内存 HTTP 客户端，默认禁止网络连接。"""

import os
import socket

import pytest
from fastapi.testclient import TestClient

from omniflow.app import create_app
from omniflow.config import Settings
from omniflow.db import Database


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # 只隔离本应用的配置名称，不读取其他凭据环境变量。
    for name in os.environ:
        if name.startswith("OMNIFLOW_"):
            monkeypatch.delenv(name)

    def deny_connect(*args, **kwargs):
        raise AssertionError("离线测试禁止真实网络连接")

    monkeypatch.setattr(socket.socket, "connect", deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connect)


@pytest.fixture
def settings(tmp_path):
    return Settings(environment="test", data_dir=tmp_path / "data")


@pytest.fixture
def database(settings):
    database = Database(settings)
    database.migrate()
    return database


@pytest.fixture
def app(settings, database):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://localhost:8443") as client:
        yield client
