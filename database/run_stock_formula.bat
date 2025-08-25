@echo off
chcp 65001 >nul

set PYTHON="C:\Users\htzl\.conda\envs\py311\python.exe"
set SCRIPT="D:\dev\file_core\database\insert_stock_formula_2.py"

:loop
echo [INFO] 开始执行 %SCRIPT% ...
%PYTHON% %SCRIPT%

if %ERRORLEVEL% neq 0 (
    echo [WARN] 脚本执行失败，错误码 %ERRORLEVEL%
    echo [INFO] 30 秒后自动重试 ...
    timeout /t 30 /nobreak >nul
    goto loop
) else (
    echo [INFO] 脚本执行完成，正常退出
    pause
    exit /b 0
)
