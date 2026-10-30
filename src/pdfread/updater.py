"""一键更新: 暂存新版本 -> 由新版本自己完成替换 -> 重启。

涉及两个进程:

1. 主进程(server 的 /api/update/apply 调用 `prepare` + `spawn`):
   预检安装目录, 把已校验的安装包解压到缓存目录的暂存区,
   写出安装计划 plan.json, 然后以暂存区里**新版本的可执行文件**
   启动安装器, 主进程随即退出。
2. 安装器(新版本 exe 的 `--install-update PLAN` 模式, 即 `install`):
   等主进程真正退出(超时则放弃, 绝不替换仍在运行的程序)
   -> 只替换安装包里存在的条目, 安装目录中的其他文件一律不碰
   -> 失败整体回滚 -> 重启应用。

安装器不在安装目录里运行, 安装目录内因此没有被占用的文件:
Windows 不会遇到文件锁, Linux 不会遇到 ETXTBSY, macOS 不会覆写
正在运行的 .app。另外, 执行替换的始终是新版本自己的安装逻辑,
修复更新器的 bug 不必再等"下下个版本"才生效。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

WAIT_TIMEOUT = 120.0   # 等主进程退出的上限(秒), 超时放弃安装


# ---------- 进程存活检测 ----------

def pid_alive(pid: int) -> bool:
    """进程是否仍在运行。

    Windows 上不能用 os.kill(pid, 0): 信号 0 与 CTRL_C_EVENT 数值相同,
    Python 会改为广播 Ctrl+C, 在无控制台的进程里必然报错(bpo-14484),
    结果与目标进程是否存活无关。因此 Windows 改用 OpenProcess。
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _pid_alive_win(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # 进程存在, 只是属于其他用户
    except OSError:
        return False
    return True


def _pid_alive_win(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    synchronize = 0x00100000
    handle = k32.OpenProcess(synchronize, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5   # ERROR_ACCESS_DENIED: 存在但无权访问
    try:
        return k32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT: 仍在运行
    finally:
        k32.CloseHandle(handle)


def wait_exit(pid: int, timeout: float | None = None) -> bool:
    """等待进程退出; 在 timeout 内退出返回 True。"""
    deadline = time.monotonic() + (WAIT_TIMEOUT if timeout is None else timeout)
    while pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)
    return True


# ---------- 安装目录 ----------

def exe_path(root: Path) -> Path:
    """安装根目录内的主程序路径(macOS 的 root 为 .app 包)。"""
    if sys.platform == "darwin":
        return root / "Contents" / "MacOS" / "pdfread"
    return root / ("pdfread.exe" if sys.platform == "win32" else "pdfread")


def layout_ok(root: Path) -> bool:
    """root 是否是一个完整的 pdfread 安装, 防止把普通目录当成安装目录。"""
    if not root.is_dir() or not exe_path(root).is_file():
        return False
    if sys.platform == "darwin":
        return root.suffix == ".app"
    return (root / "_internal").is_dir()


def preflight(target: Path) -> str:
    """检查当前安装能否自动更新; 可以则返回空串, 否则返回原因。"""
    if sys.platform == "darwin" and "AppTranslocation" in str(target):
        return ("应用正运行在 macOS 的隔离路径中，请先把 pdfread 拖到"
                "「应用程序」文件夹再打开，然后重试")
    if not layout_ok(target):
        return f"无法识别安装目录（{target}），请到发布页手动下载新版"
    if not os.access(target, os.W_OK):
        return f"安装目录没有写入权限（{target}），请到发布页手动下载新版"
    return ""


def _independent_env() -> dict:
    """启动另一个冻结程序实例时的环境变量。

    PyInstaller 6.9 起, 冻结程序启动的子进程默认被视为工作进程,
    会沿用父进程的运行时目录。安装器与重启后的应用都要比父进程
    活得更久, 必须声明为独立实例, 否则可能加载到旧版本的库。
    """
    return {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}


def _popen_detached(args: list[str], cwd: Path) -> None:
    kwargs = {"cwd": str(cwd), "env": _independent_env(),
              "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = (subprocess.CREATE_NO_WINDOW
                                   | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.DETACHED_PROCESS)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)


# ---------- 主进程: 暂存 + 启动安装器 ----------

def cleanup(workdir: Path) -> None:
    """清理以往留下的暂存区与备份(失败忽略, 下次再清)。

    这里只含安装包里的程序文件, 不含任何用户数据。
    """
    if not workdir.is_dir():
        return
    for d in [*workdir.glob("staging-*"), *workdir.glob("backup-*")]:
        shutil.rmtree(d, ignore_errors=True)


def stage(pkg: Path, staging: Path) -> Path:
    """把安装包解压到暂存区, 返回新版本的安装根目录。"""
    staging.mkdir(parents=True)
    if sys.platform == "darwin":
        root = staging / "pdfread.app"
        mnt = staging / "mnt"
        mnt.mkdir()
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-quiet",
                        "-mountpoint", str(mnt), str(pkg)],
                       check=True, timeout=120)
        try:
            subprocess.run(["ditto", str(mnt / "pdfread.app"), str(root)],
                           check=True, timeout=600)
        finally:
            if subprocess.run(["hdiutil", "detach", "-quiet", str(mnt)],
                              timeout=60).returncode != 0:
                subprocess.run(["hdiutil", "detach", "-force", "-quiet", str(mnt)],
                               timeout=60)
        subprocess.run(["xattr", "-cr", str(root)], capture_output=True, timeout=120)
    elif sys.platform == "win32":
        root = staging / "pdfread"
        with zipfile.ZipFile(pkg) as zf:
            zf.extractall(root)
        if not layout_ok(root) and layout_ok(root / "pdfread"):
            root = root / "pdfread"   # 兼容带顶层目录的压缩包
    else:
        with tarfile.open(pkg) as tf:
            try:
                tf.extractall(staging, filter="data")   # 拒绝绝对路径/越界链接
            except TypeError:   # 未带 filter 参数的旧 Python
                tf.extractall(staging)
        root = staging / "pdfread"
    if not layout_ok(root):
        raise RuntimeError("安装包内容不完整")
    return root


def prepare(pkg: Path, target: Path, pid: int, version: str,
            workdir: Path) -> tuple[Path, Path]:
    """解压到暂存区并写出安装计划, 返回 (新版本可执行文件, 计划文件)。"""
    workdir.mkdir(parents=True, exist_ok=True)
    cleanup(workdir)
    staging = workdir / f"staging-{time.time_ns()}"
    root = stage(pkg, staging)
    plan = staging / "plan.json"
    plan.write_text(json.dumps({
        "src": str(root), "target": str(target), "pid": pid,
        "version": version, "workdir": str(workdir),
    }, ensure_ascii=False), "utf-8")
    return exe_path(root), plan


def spawn(exe: Path, plan: Path) -> None:
    """以新版本可执行文件启动安装器(独立进程, 比主进程活得更久)。"""
    _popen_detached([str(exe), "--install-update", str(plan)], exe.parent)


# ---------- 安装器: 替换 + 重启 ----------

def _retry(fn, *args, attempts: int = 20, delay: float = 0.5):
    """Windows 上杀毒软件/索引服务可能短暂占用文件, 稍后重试。"""
    for i in range(attempts):
        try:
            return fn(*args)
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def _remove(p: Path) -> None:
    if p.is_symlink() or p.is_file():
        _retry(p.unlink)
    elif p.is_dir():
        _retry(shutil.rmtree, p)


def _copy(src: Path, dst: Path) -> None:
    if src.is_symlink():
        os.symlink(os.readlink(src), dst)
    elif src.is_dir():
        shutil.copytree(src, dst, symlinks=True)   # .app 内的框架依赖符号链接
    else:
        shutil.copy2(src, dst)


class RollbackFailed(RuntimeError):
    """替换失败且未能完整恢复旧版本; 旧文件保留在备份目录中。"""


def replace(src: Path, target: Path, backup: Path) -> None:
    """用 src 中的条目替换 target 中的同名条目, target 里的其他文件不动。

    旧条目先移入 backup; 任一步失败时, 只删除本次放入的新条目,
    再把旧条目移回原位, 然后重新抛出异常。
    """
    names = sorted(p.name for p in src.iterdir())
    existed = {n: (target / n).exists() or (target / n).is_symlink() for n in names}
    backup.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    try:
        for n in names:
            if existed[n]:
                _retry(shutil.move, str(target / n), str(backup / n))
                moved.append(n)
        for n in names:
            _copy(src / n, target / n)
    except BaseException as exc:
        try:
            for n in names:
                if n in moved or not existed[n]:
                    _remove(target / n)   # 只删本次放进去的内容
            for n in moved:
                _retry(shutil.move, str(backup / n), str(target / n))
        except Exception as undo:
            raise RollbackFailed(
                f"{type(exc).__name__}: {exc}; rollback failed: {undo}; "
                f"old files kept in {backup}") from exc
        raise
    shutil.rmtree(backup, ignore_errors=True)


def relaunch(target: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(target)], env=_independent_env(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        _popen_detached([str(exe_path(target))], target)


def install(plan_path: Path) -> None:
    """安装器入口(`pdfread --install-update PLAN`)。"""
    plan = json.loads(Path(plan_path).read_text("utf-8"))
    src, target = Path(plan["src"]), Path(plan["target"])
    workdir, pid = Path(plan["workdir"]), int(plan["pid"])
    log = workdir / "update.log"

    def step(msg: str) -> None:
        try:
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
        except OSError:
            pass

    step(f"installer started: v{plan.get('version', '?')} -> {target}")
    try:
        if not wait_exit(pid):
            step(f"main process {pid} still running after {WAIT_TIMEOUT:.0f}s, "
                 "aborted (nothing changed)")
            return
        step("main process exited")
        if not layout_ok(target) or not layout_ok(src):
            step("install layout check failed, aborted (nothing changed)")
            return
        try:
            replace(src, target, workdir / f"backup-{time.time_ns()}")
            step("files replaced")
        except RollbackFailed as exc:
            step(f"FAILED: {exc}")
            return   # 安装目录不完整, 不重启; 旧文件留在备份目录供手动恢复
        except Exception as exc:
            step(f"FAILED: {type(exc).__name__}: {exc}; previous version restored")
        relaunch(target)
        step("relaunched, installer done")
    except Exception as exc:
        step(f"FAILED: {type(exc).__name__}: {exc}")
