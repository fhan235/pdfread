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


def test_proxy_setting_roundtrip(client):
    r = client.post("/api/settings", json={"proxy": "http://127.0.0.1:7890"})
    assert r.status_code == 200
    s = client.get("/api/settings").json()
    assert s["proxy"] == "http://127.0.0.1:7890"
    assert server._update_proxy() == "http://127.0.0.1:7890"
    # 非法格式被拒
    r = client.post("/api/settings", json={"proxy": "127.0.0.1:7890"})
    assert r.status_code == 400
    # 清空
    r = client.post("/api/settings", json={"proxy": ""})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["proxy"] == ""


def test_apply_rejected_in_source_mode(client):
    r = client.post("/api/update/apply")
    assert r.status_code == 400
    assert "源码" in r.json()["detail"]


def test_install_target_linux(monkeypatch, tmp_path):
    from pdfread import paths
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "app" / "pdfread"))
    target, exe = paths.install_target()
    assert target == tmp_path / "app" and exe.name == "pdfread"


def test_install_target_darwin(monkeypatch, tmp_path):
    from pdfread import paths
    exe = tmp_path / "pdfread.app" / "Contents" / "MacOS" / "pdfread"
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.sys, "executable", str(exe))
    target, _ = paths.install_target()
    assert target == tmp_path / "pdfread.app"


def test_run_updater_zip_flow(tmp_path, monkeypatch):
    """更新器主流程: 等 pid -> 解压 zip -> 覆盖安装目录 -> 尝试重启。"""
    import zipfile
    from pdfread import app as appmod
    from pdfread import paths

    monkeypatch.setattr(paths.sys, "platform", "linux2")
    install = tmp_path / "install"
    install.mkdir()
    (install / "pdfread").write_text("old")
    exe = install / "pdfread"
    monkeypatch.setattr(paths.sys, "executable", str(exe))
    monkeypatch.setenv("PDFREAD_CACHE_DIR", str(tmp_path / "cache"))

    pkg = tmp_path / "pkg.tar.gz"
    import tarfile
    with tarfile.open(pkg, "w:gz") as tf:
        inner = tmp_path / "inner"
        inner.mkdir()
        (inner / "pdfread").write_text("new-binary")
        tf.add(inner, arcname="pdfread")

    launched = []
    monkeypatch.setattr(appmod.subprocess, "Popen",
                        lambda *a, **kw: launched.append(a[0]))
    # pid=999999999 不存在, os.kill 立即 OSError, 跳过等待
    appmod.run_updater(pkg, 999999999)
    assert exe.read_text() == "new-binary"
    assert launched and launched[0][0] == str(exe)
    log = (tmp_path / "cache" / "update" / "update.log").read_text()
    assert "tar extracted" in log and "relaunched" in log
