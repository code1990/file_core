@echo off
chcp 65001 >nul

REM === 配置 Python 路径（改成你实际的 Python 解释器路径）===
set "PYTHON=C:\Users\htzl\.conda\envs\py311\python.exe"

REM === 配置脚本路径 ===
set "SCRIPT=D:\dev\file_core\database\run_with_restart.py"

REM === 切换到脚本所在目录，保证相对路径可用 ===
for %%I in ("%SCRIPT%") do set "SRCDIR=%%~dpI"
cd /d "%SRCDIR%"

echo [INFO] 启动 Python 脚本: %SCRIPT%
"%PYTHON%" "%SCRIPT%"

pause
