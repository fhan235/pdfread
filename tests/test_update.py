"""版本更新: 版本比较 / 平台资产选择 / 下载地址白名单。"""

import pytest

from pdfread import server


def test_ver_tuple():
    assert server._ver_tuple("0.9.4") == (0, 9, 4)
    assert server._ver_tuple("v0.10.0") == (0, 10, 0)
    assert server._ver_tuple("0.10.0") > server._ver_tuple("0.9.9")


def test_pick_asset_by_platform(monkeypatch):
    assets = [{"name": f"pdfread-{n}", "browser_download_url": "u"}
              for n in ("windows-x64.zip", "macos-arm64.dmg", "linux-x64.tar.gz")]
    monkeypatch.setattr(server.sys, "platform", "win32")
    assert server._pick_asset(assets)["name"].endswith("windows-x64.zip")
    monkeypatch.setattr(server.sys, "platform", "darwin")
    assert server._pick_asset(assets)["name"].endswith("macos-arm64.dmg")
    monkeypatch.setattr(server.sys, "platform", "linux")
    assert server._pick_asset(assets)["name"].endswith("linux-x64.tar.gz")


def test_download_rejects_foreign_url(client, monkeypatch):
    monkeypatch.setattr(server, "_update_cache",
                        {"ts": 1e18, "data": {
                            "current": "0.9.4", "newer": True, "latest": "9.9.9",
                            "asset_name": "x.zip",
                            "asset_url": "https://evil.example.com/x.zip"}})
    r = client.post("/api/update/download")
    assert r.status_code == 400
    assert "不合法" in r.json()["detail"]


def test_download_rejects_when_no_update(client, monkeypatch):
    monkeypatch.setattr(server, "_update_cache",
                        {"ts": 1e18, "data": {"current": "0.9.4", "newer": False}})
    r = client.post("/api/update/download")
    assert r.status_code == 400
