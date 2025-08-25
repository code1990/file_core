@echo off
chcp 65001 >nul

:loop
call D:\project\PycharmProjects\pythonProject\anki\run_iwencai_date.bat
set ERR=%ERRORLEVEL%

REM 检查退出码，如果非0则重启
if not "%ERR%"=="0" (
    echo [WARN] 出现错误，等待 5 秒后重启...
    timeout /t 5
    goto loop
) else (
    echo [INFO] 脚本正常结束。
)

REM 防止窗口被关闭
pause
