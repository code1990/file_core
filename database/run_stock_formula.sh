#!/bin/bash
# run_stock_formula.sh
# CentOS7 环境
# 需求：无限循环执行 Python 脚本，出错时延时重试，关闭终端仍继续运行

PYTHON="/usr/bin/python3"   # 可以改成 `which python3` 的结果
SCRIPT="/mydata/model/mydata/insert_stock_formula_2.py"

LOG_DIR="/mydata/model/mydata/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/stock_formula_$(date +%Y%m%d_%H%M%S).log"

echo "[INFO] 启动 run_stock_formula.sh ..."
echo "[INFO] 日志文件: $LOG_FILE"

while true; do
    echo "[INFO] 开始执行 $SCRIPT ..." | tee -a "$LOG_FILE"
    $PYTHON "$SCRIPT" >>"$LOG_FILE" 2>&1
    RET=$?

    if [ $RET -ne 0 ]; then
        echo "[WARN] 脚本执行失败，错误码 $RET" | tee -a "$LOG_FILE"
        echo "[INFO] 30 秒后自动重试 ..." | tee -a "$LOG_FILE"
        sleep 30
    else
        echo "[INFO] 脚本执行完成，正常退出" | tee -a "$LOG_FILE"
        exit 0
    fi
done
