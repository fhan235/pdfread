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


class _FakeResp:
    def __init__(self, location=""):
        self.headers = {"location": location}


class _FakeClient:
    async def get(self, url, **kw):
        return _FakeResp(
            f"https://github.com/{server._UPDATE_REPO}/releases/tag/v0.9.9")


@pytest.mark.anyio
async def test_fallback_latest_from_redirect(monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "win32")
    fb = await server._fallback_latest(_FakeClient())
    assert fb["latest"] == "0.9.9"
    assert fb["asset_url"].endswith("/releases/download/v0.9.9/pdfread-windows-x64.zip")


@pytest.mark.anyio
async def test_fallback_latest_bad_location(monkeypatch):
    class Bad:
        async def get(self, url, **kw):
            return _FakeResp("https://github.com/login")
    assert await server._fallback_latest(Bad()) is None


def test_pagehide_watchdog_arm_and_cancel(client):
    hb = server._HEARTBEAT
    saved = dict(hb)
    try:
        # 未开启看门狗时 pagehide 不武装倒计时
        hb.update(last=0.0, kill_at=0.0, enabled=False)
        assert client.post("/api/pagehide").status_code == 200
        assert hb["kill_at"] == 0.0
        # 开启后 pagehide 武装, 心跳取消
        hb["enabled"] = True
        client.post("/api/pagehide")
        assert hb["kill_at"] > 0
        client.post("/api/heartbeat")
        assert hb["kill_at"] == 0.0 and hb["last"] > 0
    finally:
        hb.update(saved)
