import subprocess
import time
import sys
import os

# === 修改为 Linux 下实际 Python 路径 ===
PYTHON = "/usr/bin/python3.12"

# === 修改为你的脚本路径 ===
SCRIPT = "/mydata/model/mydata/insert_stock_change_detail_2.py"

def run_script():
    while True:
        try:
            print("🚀 启动 insert_stock_change_detail.py ...")
            # 启动子进程
            result = subprocess.run([PYTHON, SCRIPT], check=True)
            print("✅ 脚本正常退出，准备结束监控")
            break
        except subprocess.CalledProcessError as e:
            print(f"❌ 脚本异常退出，错误码 {e.returncode}，3秒后重启...")
            time.sleep(3)
        except Exception as e:
            print(f"⚠️ 未知错误: {e}，3秒后重启...")
            time.sleep(3)

if __name__ == "__main__":
    run_script()
