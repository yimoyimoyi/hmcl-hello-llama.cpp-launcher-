@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: 自动检测并使用 uv .venv 或系统 Python 启动（无窗口模式）
:: main.py 内置 Qt 插件路径修复，pythonw 下也能正常运行
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
) else if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" "main.py"
) else (
    start "" pythonw "main.py" 2>nul || start "" python "main.py"
)
