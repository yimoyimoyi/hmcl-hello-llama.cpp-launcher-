"""
下载模块：Release 下载线程 + 显存检测线程。
使用 aria2c（多源+多分片）下载文件，自动下载 aria2c 到 assets/。
"""
import os
import sys
import re
import json
import time
import zipfile
import shutil
import urllib.request
import subprocess
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from .config import (
    BASE_DIR, ASSETS_DIR, BIN_DIR, _ARIA2C_EXE, _ARIA2C_ZIP, _ARIA2C_ZIP_URL,
    GITHUB_API_URL, MIRROR_BASE_URLS, PROXY_HOST, PROXY_PORT, _get_opener,
)
from .platform import CREATE_NO_WINDOW, set_opener
from .backends import get_backends_for_platform


# ═══════════════════════════════════════════════
#  ReleaseDownloadThread
# ═══════════════════════════════════════════════

class ReleaseDownloadThread(QThread):
    """后台下载 llama.cpp Release。使用 aria2c 下载。"""
    status_signal    = pyqtSignal(str)      # 状态更新
    progress_signal  = pyqtSignal(int, int) # (cur, total)
    finished_signal  = pyqtSignal(str)      # 下载成功 → 路径
    error_signal     = pyqtSignal(str)      # 下载失败 → 错误信息
    raw_signal       = pyqtSignal(str)      # aria2c 原始输出（刷新模式）
    assets_signal    = pyqtSignal(list)     # 可用资源列表 [{name,size,url},...]

    def __init__(self, target_dir: str, backend_id: str = "",
                 retry_count: int = 3, timeout: int = 300):
        super().__init__()
        self._target_dir     = target_dir
        self._backend_id     = backend_id
        self._retry_count    = retry_count
        self._timeout        = timeout
        self._cancel         = False
        self._current_proc   = None
        self._asset_name     = ""           # 指定下载文件名（为空则自动匹配）

    # ══════════════════════════════════════
    #  aria2c 自下载
    # ══════════════════════════════════════

    def _ensure_aria2c(self) -> bool:
        """确保 aria2c 可用。Windows 自动下载到 assets/，Linux 尝试 apt 自动安装。"""
        if sys.platform != "win32":
            aria2_path = shutil.which("aria2c")
            if aria2_path:
                return True
            # Linux: 尝试 apt 自动安装（可能需要 sudo 权限）
            self.status_signal.emit("⬇ 正在尝试自动安装 aria2c（可能需要 sudo 密码）...")
            try:
                subprocess.run(
                    ["sudo", "-n", "apt", "install", "-y", "aria2"],
                    capture_output=True, text=True, timeout=60,
                )
                if shutil.which("aria2c"):
                    self.status_signal.emit("✅ aria2c 已通过 apt 自动安装")
                    return True
            except FileNotFoundError:
                self.status_signal.emit("⚠ 未找到 apt 包管理器，尝试 pacman...")
                try:
                    subprocess.run(
                        ["sudo", "-n", "pacman", "-S", "--noconfirm", "aria2"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if shutil.which("aria2c"):
                        self.status_signal.emit("✅ aria2c 已通过 pacman 自动安装")
                        return True
                except Exception:
                    pass
            except subprocess.CalledProcessError:
                self.status_signal.emit("⚠ sudo apt 需要手动授权")
            except Exception:
                pass
            self.error_signal.emit(
                "⚠ aria2c 未安装，下载需 aria2c 支持。\n"
                "请手动运行以下命令之一：\n"
                "  Ubuntu/Debian: sudo apt install aria2\n"
                "  Fedora:        sudo dnf install aria2\n"
                "  Arch Linux:    sudo pacman -S aria2\n"
                "  macOS:         brew install aria2\n"
                "安装后重启启动器即可正常下载。")
            return False

        if os.path.isfile(_ARIA2C_EXE):
            return True

        self.status_signal.emit("⬇ 正在下载 aria2c（首次使用）...")
        opener = _get_opener()
        try:
            hdr = urllib.request.Request(
                _ARIA2C_ZIP_URL,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with opener.open(hdr) as resp:
                with open(_ARIA2C_ZIP, "wb") as f:
                    f.write(resp.read())
            with zipfile.ZipFile(_ARIA2C_ZIP, "r") as zf:
                for member in zf.namelist():
                    if member.lower().endswith("aria2c.exe"):
                        exe_bytes = zf.read(member)
                        with open(_ARIA2C_EXE, "wb") as ef:
                            ef.write(exe_bytes)
                        try:
                            os.remove(_ARIA2C_ZIP)
                        except Exception:
                            pass
                        return True
            self.error_signal.emit("aria2c 解压失败：zip 中未找到 aria2c.exe")
            return False
        except Exception as e:
            self.error_signal.emit(f"下载 aria2c 失败: {e}")
            return False

    # ══════════════════════════════════════
    #  Cancel
    # ══════════════════════════════════════

    def cancel(self):
        self._cancel = True
        if self._current_proc:
            try:
                self._current_proc.terminate()
            except Exception:
                pass
            try:
                self._current_proc.wait(timeout=5)
            except Exception:
                try:
                    self._current_proc.kill()
                except Exception:
                    pass

    # ══════════════════════════════════════
    #  aria2c 下载核心
    # ══════════════════════════════════════

    def _aria2c_download(self, urls: list, dest: str, fname: str) -> bool:
        """用 aria2c 下载文件，实时解析 stderr 获取百分比与速度。"""
        aria2c = shutil.which("aria2c") if sys.platform != "win32" else _ARIA2C_EXE
        if not aria2c or not os.path.isfile(aria2c):
            self.status_signal.emit("❌ 找不到 aria2c")
            self.raw_signal.emit(f"[aria2c] 路径无效: {aria2c}")
            return False

        out_file = os.path.join(dest, fname)
        os.makedirs(dest, exist_ok=True)

        # aria2c 参数：多连接下载 + 低调日志
        cmd = [aria2c,
               "--continue=true",
               "--split=16", "--max-connection-per-server=16",
               "--min-split-size=1M",          # 强制分片，确保多连接生效
               "--no-proxy=true",
               "--max-tries=5", "--retry-wait=3",
               "--connect-timeout=15", "--timeout=60",
               "--allow-overwrite=true",
               "--summary-interval=1",         # 每秒汇报进度
               "--console-log-level=warn",     # 仅输出警告和错误
               "--dir=" + dest, "--out=" + fname]
        for u in urls:
            cmd.append(u)

        self.raw_signal.emit(f"[aria2c] {' '.join(cmd)}")

        try:
            self._current_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # 合并到 stdout，只需读一个流
                stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=(CREATE_NO_WINDOW if sys.platform == "win32" else 0),
            )

            total_size = 0
            last_pct = -1
            start_time = time.time()

            for line in iter(self._current_proc.stdout.readline, ""):
                line = line.rstrip("\n\r")
                if self._cancel:
                    break

                # 每一行都转发到控制台
                self.raw_signal.emit(f"[aria2c] {line}")

                # 从 aria2c 输出中提取总大小
                m_size = re.search(r"\((\d+)\s*[Bb]ytes?\)", line)
                if m_size and total_size == 0:
                    total_size = int(m_size.group(1))

                # 提取百分比 (GID#1 45%)
                m = re.search(r"\(#\d+\)[^\d]*(\d+)%", line)
                if m:
                    pct = int(m.group(1))
                    if pct != last_pct:
                        last_pct = pct
                        elapsed = time.time() - start_time
                        if total_size > 0:
                            cur = int(total_size * pct / 100)
                            self.progress_signal.emit(cur, total_size)
                        speed_m = re.search(r"(\d+(?:\.\d+)?)\s*(MiB|KiB)/s", line)
                        speed_str = f" {speed_m.group(1)}{speed_m.group(2)}/s" if speed_m else ""
                        self.status_signal.emit(f"⬇ {pct}%{speed_str} ({elapsed:.0f}s)")

            if self._current_proc.stdout:
                self._current_proc.stdout.close()
            rc = self._current_proc.wait()
            self._current_proc = None

            if rc != 0:
                self.raw_signal.emit(f"[aria2c] 退出码: {rc}")
            return rc == 0 and os.path.isfile(out_file) and os.path.getsize(out_file) > 0
        except Exception as e:
            self._current_proc = None
            self.raw_signal.emit(f"[aria2c] 异常: {e}")
            self.status_signal.emit(f"❌ aria2c 错误: {e}")
            return False

    # ══════════════════════════════════════
    #  Release JSON 获取
    # ══════════════════════════════════════

    def _fetch_release_json(self) -> Optional[dict]:
        """获取 Release JSON（优先读缓存，缓存有效期 30 分钟）。"""
        from .config import RELEASE_CACHE_PATH
        # 尝试读缓存
        try:
            if os.path.isfile(RELEASE_CACHE_PATH):
                mtime = os.path.getmtime(RELEASE_CACHE_PATH)
                if time.time() - mtime < 1800:  # 30 分钟
                    with open(RELEASE_CACHE_PATH, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    if cached.get("tag_name"):
                        self.status_signal.emit(f"📦 使用缓存 Release: {cached.get('tag_name','')}")
                        return cached
        except Exception:
            pass

        opener = _get_opener()
        for url in [GITHUB_API_URL]:
            if self._cancel:
                return None
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with opener.open(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    # 写入缓存
                    try:
                        with open(RELEASE_CACHE_PATH, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False)
                    except Exception:
                        pass
                    return data
            except Exception as e:
                self.status_signal.emit(f"⚠ 获取 Release 信息失败 ({url}): {e}")
        return None

    # ══════════════════════════════════════
    #  镜像 URL 构造
    # ══════════════════════════════════════

    def _resolve_mirror_urls(self, url: str):
        """返回 [原始URL, ...镜像URLs]"""
        urls = [url]
        fname = url.rsplit("/", 1)[-1]
        for base in MIRROR_BASE_URLS:
            urls.append(base + fname)
        return urls

    # ══════════════════════════════════════
    #  单文件下载+解压
    # ══════════════════════════════════════

    def _download_file_aria2(self, url: str, fname: str, extract_to: str) -> bool:
        """用 aria2c（多镜像 + 自动重试）下载单个文件并解压（支持 .zip 和 .tar.gz）。"""
        urls = self._resolve_mirror_urls(url)
        self.status_signal.emit(f"⬇ 开始下载 {fname}...")

        # 尝试多次下载
        for attempt in range(self._retry_count):
            if self._cancel:
                return False
            if attempt > 0:
                self.status_signal.emit(f"🔄 重试 {attempt+1}/{self._retry_count}: {fname}")
                time.sleep(2)
            ok = self._aria2c_download(urls, ASSETS_DIR, fname)
            if ok:
                break
        else:
            self.status_signal.emit(f"❌ 下载失败: {fname}")
            return False

        # 解压
        local_path = os.path.join(ASSETS_DIR, fname)
        if not os.path.isfile(local_path):
            return False
        self.status_signal.emit(f"📦 正在解压 {fname}...")
        try:
            if fname.endswith(".tar.gz") or fname.endswith(".tgz"):
                self._extract_targz(local_path, extract_to)
            elif fname.endswith(".zip"):
                self.raw_signal.emit(f"[zip] 开始解压: {fname} → {extract_to}")
                with zipfile.ZipFile(local_path, "r") as zf:
                    for m in zf.namelist():
                        try:
                            zf.extract(m, extract_to)
                        except Exception as e:
                            self.raw_signal.emit(f"[zip] 跳过 {m}: {e}")
                self.raw_signal.emit("[zip] 解压完成")
            else:
                self.raw_signal.emit(f"[copy] 复制: {fname} → {extract_to}")
                shutil.copy2(local_path, extract_to)
            try:
                os.remove(local_path)
                self.raw_signal.emit(f"[clean] 已删除: {fname}")
            except Exception as e:
                self.raw_signal.emit(f"[clean] 删除失败: {fname} — {e}")
            return True
        except Exception as e:
            self.raw_signal.emit(f"[extract] 解压异常: {e}")
            self.status_signal.emit(f"❌ 解压失败: {e}")
            return False

    def _extract_targz(self, path: str, extract_to: str):
        """解压 .tar.gz 文件（安全模式：过滤危险路径，详细日志）。"""
        import tarfile
        self.raw_signal.emit(f"[tar] 开始解压: {os.path.basename(path)} → {extract_to}")
        try:
            with tarfile.open(path, "r:gz") as tar:
                members = tar.getmembers()
                safe = []
                skipped = 0
                for m in members:
                    # 拒绝绝对路径和 ".." 穿越
                    if m.name.startswith("/") or ".." in m.name:
                        self.raw_signal.emit(f"[tar] 跳过危险路径: {m.name}")
                        skipped += 1
                        continue
                    safe.append(m)
                self.raw_signal.emit(f"[tar] 共 {len(members)} 个条目, 跳过 {skipped} 个, 解压 {len(safe)} 个")
                for m in safe:
                    try:
                        tar.extract(m, path=extract_to, set_attrs=False)
                    except Exception as e:
                        self.raw_signal.emit(f"[tar] 跳过 {m.name}: {e}")
                self.raw_signal.emit(f"[tar] 解压完成")
        except Exception as e:
            self.raw_signal.emit(f"[tar] 解压异常: {e}")
            raise  # 继续向上抛出，让 _download_file_aria2 处理

    # ══════════════════════════════════════
    #  Main run
    # ══════════════════════════════════════

    def run(self):
        """两阶段下载：
        Phase 1 — 无 _asset_url 时：获取 Release → 缓存 → 发送可用列表 → 退出
        Phase 2 — 有 _asset_url 时：直接下载指定文件
        """
        if not self._ensure_aria2c():
            return

        # ── Phase 1: 获取 Release 并列出可用文件 ──
        if not getattr(self, '_asset_url', None):
            self.status_signal.emit("🔍 正在获取 Release 信息...")
            release = self._fetch_release_json()
            if not release:
                self.error_signal.emit("无法获取 Release 信息，请检查网络或手动下载。")
                return

            assets = release.get("assets", [])
            if not assets:
                self.error_signal.emit("Release 中没有资源文件。")
                return

            # 筛选当前平台的可用文件
            from .backends import get_backends_for_platform
            import platform as _platform
            os_name = sys.platform
            arch = _platform.machine().lower()
            all_backends = get_backends_for_platform(os_name, arch)

            ext_filter = ".zip" if os_name == "win32" else ".tar.gz"
            available = []
            for a in assets:
                name = a.get("name", "")
                if ext_filter not in name:
                    continue
                # 匹配后端
                for b in all_backends:
                    if b["suffix"] in name:
                        size_mb = round(a.get("size", 0) / 1048576, 1)
                        available.append({
                            "name": name,
                            "size": size_mb,
                            "url": a.get("browser_download_url", ""),
                            "backend_id": b["id"],
                            "backend_label": b["label"],
                        })
                        break

            if not available:
                self.error_signal.emit("当前平台无可下载文件。")
                return

            self.raw_signal.emit(f"📋 Release: {release.get('tag_name','?')} — {len(available)} 个可用文件：")
            self.assets_signal.emit(available)
            self.status_signal.emit("👆 在控制台点击文件名即可下载")
            return

        # ── Phase 2: 下载指定文件 ──
        target = self._target_dir or BIN_DIR
        os.makedirs(target, exist_ok=True)

        self.raw_signal.emit(f"⬇ 开始下载: {self._asset_name} ({self._asset_size}MB)")
        if not self._download_file_aria2(self._asset_url, self._asset_name, target):
            self.error_signal.emit(f"下载失败: {self._asset_name}")
            return

        self.status_signal.emit("✅ 下载完成")
        self.finished_signal.emit(target)

    def set_asset(self, name: str, url: str, size: float = 0):
        """设置要下载的资产，使线程进入 Phase 2。"""
        self._asset_name = name
        self._asset_url = url
        self._asset_size = size

# ═══════════════════════════════════════════════
#  VramCheckThread
# ═══════════════════════════════════════════════

class VramCheckThread(QThread):
    """仅检测显存信息，不进行层数推算。"""
    result_signal = pyqtSignal(int, int)  # (total_mb, free_mb)

    def run(self):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
                creationflags=(CREATE_NO_WINDOW if sys.platform == "win32" else 0),
            )
            line = r.stdout.strip().split("\n")[0]  # 取第一块 GPU
            parts = line.split(",")
            total = int(parts[0].strip())
            free  = int(parts[1].strip())
            self.result_signal.emit(total, free)
        except Exception:
            self.result_signal.emit(0, 0)
