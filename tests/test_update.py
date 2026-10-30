"""版本更新: 检查 / 下载校验 / 一键安装(updater)。"""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from types import SimpleNamespace

import pytest

from pdfread import server, updater


# ---------- 版本检查 ----------

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


def test_update_check_reports_apply_block_in_source_mode(client, monkeypatch):
    async def fake_latest(force=False):
        return {"current": "1.0.7", "newer": True, "latest": "9.9.9", "error": ""}
    monkeypatch.setattr(server, "_latest_release", fake_latest)
    d = client.get("/api/update/check").json()
    assert "源码" in d["apply_block"]


# ---------- 下载与校验 ----------

PKG = b"fake-installer-bytes" * 1000
NAME = "pdfread-linux-x64.tar.gz"


def _release(monkeypatch, ver="9.9.9", url_ver=None, name=NAME):
    url = (f"https://github.com/{server._UPDATE_REPO}/releases/download"
           f"/v{url_ver or ver}/{name}")
    monkeypatch.setattr(server, "_update_cache", {"ts": 1e18, "data": {
        "current": "1.0.7", "newer": True, "latest": ver,
        "asset_name": name, "asset_url": url}})


class _Stream:
    def __init__(self, body):
        self.status_code, self.headers, self._b = 200, {"content-length": str(len(body))}, body

    async def aiter_bytes(self, n):
        for i in range(0, len(self._b), n):
            yield self._b[i:i + n]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _DLClient:
    """模拟 GitHub: get 返回 SHA256SUMS.txt, stream 返回安装包。"""

    def __init__(self, sums, body=PKG):
        self.sums, self.body, self.downloads = sums, body, 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        assert url.endswith("/SHA256SUMS.txt")
        text = self.sums or ""
        return SimpleNamespace(status_code=200 if self.sums is not None else 404,
                               content=text.encode(), text=text)

    def stream(self, method, url, **kw):
        self.downloads += 1
        return _Stream(self.body)


def _use(monkeypatch, fake):
    monkeypatch.setattr(server, "_update_client", lambda *a, **kw: fake)
    return fake


def _sums(digest=None):
    digest = digest or hashlib.sha256(PKG).hexdigest()
    return f"{'0' * 64}  pdfread-windows-x64.zip\n{digest}  {NAME}\n"


def test_download_rejects_foreign_url(client, monkeypatch):
    monkeypatch.setattr(server, "_update_cache",
                        {"ts": 1e18, "data": {
                            "current": "0.9.4", "newer": True, "latest": "9.9.9",
                            "asset_name": NAME,
                            "asset_url": "https://evil.example.com/x.zip"}})
    r = client.post("/api/update/download")
    assert r.status_code == 400
    assert "不合法" in r.json()["detail"]


def test_download_rejects_url_of_other_version(client, monkeypatch):
    _release(monkeypatch, ver="9.9.9", url_ver="1.0.0")
    assert client.post("/api/update/download").status_code == 400


def test_download_rejects_when_no_update(client, monkeypatch):
    monkeypatch.setattr(server, "_update_cache",
                        {"ts": 1e18, "data": {"current": "0.9.4", "newer": False}})
    r = client.post("/api/update/download")
    assert r.status_code == 400


def test_download_verifies_checksum_and_reuses_file(client, monkeypatch, tmp_path):
    _release(monkeypatch)
    fake = _use(monkeypatch, _DLClient(_sums()))
    r = client.post("/api/update/download")
    assert r.status_code == 200, r.text
    dest = server.cache_dir() / "downloads" / NAME
    assert dest.read_bytes() == PKG
    assert not (dest.parent / (NAME + ".part")).exists()
    # 再次调用: 已有文件校验一致, 不重复下载
    assert client.post("/api/update/download").status_code == 200
    assert fake.downloads == 1


def test_download_discards_bad_checksum(client, monkeypatch):
    _release(monkeypatch)
    _use(monkeypatch, _DLClient(_sums(digest="ab" * 32)))
    r = client.post("/api/update/download")
    assert r.status_code == 502 and "校验失败" in r.json()["detail"]
    d = server.cache_dir() / "downloads"
    assert not (d / NAME).exists() and not (d / (NAME + ".part")).exists()


def test_download_requires_checksum_file(client, monkeypatch):
    _release(monkeypatch)
    fake = _use(monkeypatch, _DLClient(None))
    r = client.post("/api/update/download")
    assert r.status_code == 502 and "校验值" in r.json()["detail"]
    assert fake.downloads == 0   # 没有校验值就不下载


# ---------- 一键安装接口 ----------

def test_apply_rejected_in_source_mode(client):
    r = client.post("/api/update/apply")
    assert r.status_code == 400
    assert "源码" in r.json()["detail"]


@pytest.fixture
def frozen_install(monkeypatch, tmp_path):
    """伪装成打包后的 Linux 安装。"""
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(server.sys, "frozen", True, raising=False)
    target = _fake_install(tmp_path / "install")
    monkeypatch.setattr(server.sys, "executable", str(target / "pdfread"))
    _release(monkeypatch)
    _use(monkeypatch, _DLClient(_sums()))
    return target


def test_apply_spawns_installer_then_exits(client, monkeypatch, frozen_install):
    calls = {}

    def fake_prepare(pkg, target, pid, version, workdir):
        calls["prepare"] = (pkg.read_bytes(), target, pid, version)
        return workdir / "new-exe", workdir / "plan.json"

    monkeypatch.setattr(updater, "prepare", fake_prepare)
    monkeypatch.setattr(updater, "spawn", lambda exe, plan: calls.setdefault("spawn", exe))
    monkeypatch.setattr(server, "_schedule_exit", lambda: calls.setdefault("exit", True))

    r = client.post("/api/update/apply")
    assert r.status_code == 200, r.text
    assert calls["prepare"] == (PKG, frozen_install, os.getpid(), "9.9.9")
    assert calls["spawn"].name == "new-exe" and calls["exit"]
    marker = json.loads((server.cache_dir() / "update" / "pending.json").read_text("utf-8"))
    assert marker["version"] == "9.9.9"


def test_apply_spawn_failure_keeps_app_running(client, monkeypatch, frozen_install):
    monkeypatch.setattr(updater, "prepare",
                        lambda pkg, t, pid, v, w: (w / "exe", w / "plan.json"))

    def boom(exe, plan):
        raise OSError("blocked by antivirus")

    exited = []
    monkeypatch.setattr(updater, "spawn", boom)
    monkeypatch.setattr(server, "_schedule_exit", lambda: exited.append(1))
    r = client.post("/api/update/apply")
    assert r.status_code == 500 and "安装器" in r.json()["detail"]
    assert not exited
    assert not (server.cache_dir() / "update" / "pending.json").exists()


# ---------- 其他 ----------

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


# ---------- updater: 进程检测 ----------

def test_pid_alive_real_platform():
    """在真实平台上运行(CI 的 Windows runner 会实测 OpenProcess 分支)。"""
    assert updater.pid_alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not updater.pid_alive(p.pid)
    assert not updater.pid_alive(0)


def test_wait_exit_times_out_for_live_process(monkeypatch):
    monkeypatch.setattr(updater, "pid_alive", lambda pid: True)
    assert not updater.wait_exit(1234, timeout=0.2)
    monkeypatch.setattr(updater, "pid_alive", lambda pid: False)
    assert updater.wait_exit(1234, timeout=0.2)


# ---------- updater: 替换安装目录 ----------

@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")


def _fake_install(root, tag="old"):
    (root / "_internal").mkdir(parents=True, exist_ok=True)
    (root / "pdfread").write_text(f"{tag}-exe")
    (root / "_internal" / "lib.so").write_text(f"{tag}-lib")
    return root


def test_replace_only_touches_package_entries(linux, tmp_path):
    """安装目录是「下载」文件夹时, 用户自己的文件必须原样保留。"""
    target = _fake_install(tmp_path / "Downloads")
    (target / "_internal" / "stale.so").write_text("removed in new version")
    (target / "my-thesis.pdf").write_text("user data")
    (target / "photos").mkdir()
    (target / "photos" / "a.jpg").write_text("img")
    src = _fake_install(tmp_path / "new", tag="new")
    backup = tmp_path / "backup"

    updater.replace(src, target, backup)

    assert (target / "pdfread").read_text() == "new-exe"
    assert (target / "_internal" / "lib.so").read_text() == "new-lib"
    assert not (target / "_internal" / "stale.so").exists()   # 不残留旧文件
    assert (target / "my-thesis.pdf").read_text() == "user data"
    assert (target / "photos" / "a.jpg").read_text() == "img"
    assert not backup.exists()


def test_replace_rolls_back_partial_copy(linux, tmp_path, monkeypatch):
    """_internal 已拷入、主程序拷贝失败: 删除新 _internal, 恢复整个旧版本。"""
    target = _fake_install(tmp_path / "app")
    (target / "notes.txt").write_text("keep")
    src = _fake_install(tmp_path / "new", tag="new")

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(updater.shutil, "copy2", boom)

    with pytest.raises(OSError):
        updater.replace(src, target, tmp_path / "backup")
    assert (target / "pdfread").read_text() == "old-exe"
    assert (target / "_internal" / "lib.so").read_text() == "old-lib"
    assert (target / "notes.txt").read_text() == "keep"


# ---------- updater: 安装器主流程 ----------

def _plan(tmp_path, target, src, pid=4321):
    workdir = tmp_path / "cache" / "update"
    workdir.mkdir(parents=True, exist_ok=True)
    plan = workdir / "plan.json"
    plan.write_text(json.dumps({"src": str(src), "target": str(target), "pid": pid,
                                "version": "9.9.9", "workdir": str(workdir)}), "utf-8")
    return plan, workdir / "update.log"


@pytest.fixture
def launched(monkeypatch):
    out = []
    monkeypatch.setattr(updater, "_popen_detached", lambda args, cwd: out.append(args))
    return out


def test_install_flow(linux, tmp_path, monkeypatch, launched):
    monkeypatch.setattr(updater, "pid_alive", lambda pid: False)
    target = _fake_install(tmp_path / "app")
    (target / "notes.txt").write_text("keep")
    src = _fake_install(tmp_path / "staging" / "pdfread", tag="new")
    plan, log = _plan(tmp_path, target, src)

    updater.install(plan)

    assert (target / "pdfread").read_text() == "new-exe"
    assert (target / "notes.txt").read_text() == "keep"
    assert launched == [[str(target / "pdfread")]]
    text = log.read_text("utf-8")
    assert "main process exited" in text and "files replaced" in text
    assert "relaunched" in text


def test_install_aborts_while_main_process_alive(linux, tmp_path, monkeypatch, launched):
    monkeypatch.setattr(updater, "pid_alive", lambda pid: True)
    monkeypatch.setattr(updater, "WAIT_TIMEOUT", 0.2)
    target = _fake_install(tmp_path / "app")
    src = _fake_install(tmp_path / "staging" / "pdfread", tag="new")
    plan, log = _plan(tmp_path, target, src)

    updater.install(plan)

    assert (target / "pdfread").read_text() == "old-exe"   # 绝不替换运行中的程序
    assert not launched
    assert "aborted" in log.read_text("utf-8")


def test_install_refuses_unrecognized_target(linux, tmp_path, monkeypatch, launched):
    monkeypatch.setattr(updater, "pid_alive", lambda pid: False)
    target = tmp_path / "Documents"
    target.mkdir()
    (target / "pdfread").write_text("not really an install")   # 缺 _internal
    src = _fake_install(tmp_path / "staging" / "pdfread", tag="new")
    plan, log = _plan(tmp_path, target, src)

    updater.install(plan)

    assert (target / "pdfread").read_text() == "not really an install"
    assert not launched
    assert "layout check failed" in log.read_text("utf-8")


def test_install_failure_restores_and_relaunches_old(linux, tmp_path, monkeypatch, launched):
    monkeypatch.setattr(updater, "pid_alive", lambda pid: False)

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(updater.shutil, "copy2", boom)
    target = _fake_install(tmp_path / "app")
    src = _fake_install(tmp_path / "staging" / "pdfread", tag="new")
    plan, log = _plan(tmp_path, target, src)

    updater.install(plan)

    assert (target / "pdfread").read_text() == "old-exe"
    assert launched   # 恢复后的旧版本照常启动, 用户不会无程序可用
    assert "previous version restored" in log.read_text("utf-8")


# ---------- updater: 暂存 ----------

def _make_tar(path, members):
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_stage_tar(linux, tmp_path):
    pkg = _make_tar(tmp_path / "pkg.tar.gz", {
        "pdfread/pdfread": b"exe", "pdfread/_internal/lib.so": b"lib"})
    root = updater.stage(pkg, tmp_path / "staging")
    assert root == tmp_path / "staging" / "pdfread"
    assert updater.layout_ok(root)


def test_stage_tar_rejects_path_traversal(linux, tmp_path):
    pkg = _make_tar(tmp_path / "evil.tar.gz", {
        "pdfread/pdfread": b"exe", "pdfread/_internal/x": b"x", "../escaped": b"!"})
    with pytest.raises(tarfile.TarError):
        updater.stage(pkg, tmp_path / "staging")
    assert not (tmp_path / "escaped").exists()


def test_stage_zip_windows_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    pkg = tmp_path / "pkg.zip"
    with zipfile.ZipFile(pkg, "w") as zf:   # CI 打出的 zip 没有顶层目录
        zf.writestr("pdfread.exe", "exe")
        zf.writestr("_internal/python312.dll", "dll")
    root = updater.stage(pkg, tmp_path / "staging")
    assert (root / "pdfread.exe").is_file() and updater.layout_ok(root)


def test_stage_rejects_incomplete_package(linux, tmp_path):
    pkg = _make_tar(tmp_path / "pkg.tar.gz", {"pdfread/README": b"no exe"})
    with pytest.raises(RuntimeError):
        updater.stage(pkg, tmp_path / "staging")


def test_prepare_writes_plan_and_cleans_old_staging(linux, tmp_path):
    workdir = tmp_path / "update"
    (workdir / "staging-old").mkdir(parents=True)
    (workdir / "backup-old").mkdir()
    pkg = _make_tar(tmp_path / "pkg.tar.gz", {
        "pdfread/pdfread": b"exe", "pdfread/_internal/lib.so": b"lib"})
    target = _fake_install(tmp_path / "app")

    exe, plan = updater.prepare(pkg, target, 4321, "9.9.9", workdir)

    assert exe.read_bytes() == b"exe"
    data = json.loads(plan.read_text("utf-8"))
    assert data["target"] == str(target) and data["pid"] == 4321
    assert data["workdir"] == str(workdir)
    assert not (workdir / "staging-old").exists() and not (workdir / "backup-old").exists()


def test_preflight(linux, tmp_path):
    assert updater.preflight(_fake_install(tmp_path / "app")) == ""
    plain = tmp_path / "Desktop"
    plain.mkdir()
    assert "无法识别" in updater.preflight(plain)
