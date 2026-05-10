"""
启动线程：在后台运行 llama.cpp 进程并捕获输出。
"""
import os
import sys
import subprocess
import threading

from PyQt5.QtCore import QThread, pyqtSignal

from .platform import CREATE_NO_WINDOW


class LaunchThread(QThread):
    output_signal   = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    error_signal    = pyqtSignal(str)

    def __init__(self, args: list, cwd: str):
        super().__init__()
        self.args       = args
        self.cwd        = cwd
        self._proc      = None
        self._stop_flag = threading.Event()

    def run(self):
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = CREATE_NO_WINDOW

            self._proc = subprocess.Popen(
                self.args,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )

            for line in iter(self._proc.stdout.readline, ""):
                if self._stop_flag.is_set():
                    break
                line = line.rstrip("\n\r")
                if line:
                    self.output_signal.emit(line)

            self._proc.stdout.close()
            rc = self._proc.wait()
            # 总是发出 finished_signal，确保 UI 能正确重置按钮状态
            self.finished_signal.emit(rc)
        except Exception as e:
            if not self._stop_flag.is_set():
                self.error_signal.emit(str(e))

    def stop(self):
        self._stop_flag.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def send_input(self, text: str):
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(text + "\n")
                self._proc.stdin.flush()
            except Exception:
                pass
