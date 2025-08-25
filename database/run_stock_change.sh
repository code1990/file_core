#!/bin/bash
# ===========================================
# run_stock_change.sh
# 用于在 Linux (CentOS7) 环境运行 Python 脚本
# ===========================================

# === 配置 Python 路径（请修改成实际路径）===
PYTHON="/usr/bin/python3.12"

# === 配置脚本路径 ===
SCRIPT="/mydata/model/mydata/run_with_restart.py"

# === 切换到脚本所在目录，保证相对路径可用 ===
SRCDIR=$(dirname "$SCRIPT")
cd "$SRCDIR" || exit 1

echo "[INFO] 启动 Python 脚本: $SCRIPT"
$PYTHON "$SCRIPT"

# === 如果需要类似 Windows 的 pause，可以模拟等待输入 ===
read -p "[INFO] 脚本执行完成，按回车键退出..." _
