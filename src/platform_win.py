"""
Windows 平台特定函数：设备检测、启动参数、.bat 脚本生成。
"""
import os
import sys
import subprocess
import urllib.request

# Windows 专用标志
CREATE_NO_WINDOW = 0x08000000

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def set_opener(opener):
    global _OPENER
    _OPENER = opener


def _open_folder(path: str):
    """Windows 用 startfile 打开目录。"""
    os.startfile(path)


def _generate_bat_content(cmd_parts: list) -> str:
    """将命令列表转为 .bat 文件内容。"""
    escaped = []
    for a in cmd_parts:
        if any(c in a for c in (' ', '"', '&', '|', '<', '>', '%', '^')):
            escaped.append(f'"{a}"')
        else:
            escaped.append(a)
    return "@echo off\n" + " ".join(escaped) + "\npause\n"


def _save_script(cmd_parts: list, parent_dir: str) -> str:
    """在 parent_dir 下保存 .bat 文件，返回路径。"""
    bat_path = os.path.join(parent_dir, "launch.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(_generate_bat_content(cmd_parts))
    return bat_path
