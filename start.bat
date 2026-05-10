@echo off
cd /d "%~dp0"

:: 自动检测 Python 环境优先级：uv .venv > 系统 pip
if exist ".venv\Scripts\python.exe" (
    start "" /b ".venv\Scripts\pythonw.exe" "main.py" 2>nul || start "" /b ".venv\Scripts\python.exe" "main.py"
) else (
    start "" /b pythonw "main.py" 2>nul || start "" /b python "main.py"
)
exit
