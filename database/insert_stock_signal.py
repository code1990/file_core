import sqlite3
from pathlib import Path

# === 配置 ===
db_file = "stock.db"  # SQLite 数据库文件
root_dir = Path(r"D:\dev\dev_123\999")  # 根目录

# === 打开数据库（开启 WAL 并发模式） ===
conn = sqlite3.connect(db_file, check_same_thread=False)
cur = conn.cursor()
cur.execute("PRAGMA journal_mode=WAL;")
cur.execute("PRAGMA synchronous=NORMAL;")

# === 遍历 txt 文件 ===
for txt_file in root_dir.glob("*.txt"):
    signal_name = txt_file.stem  # 文件名（去掉扩展名）
    print(f"📂 处理信号文件: {signal_name}")

    with open(txt_file, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 2:
                continue  # 跳过无效行

            stock_code, trade_date = parts[0], parts[1]

            try:
                cur.execute("""
                    INSERT OR IGNORE INTO t_stock_signal
                    (trade_date, stock_code, signal_name, signal_value)
                    VALUES (?, ?, ?, ?)
                """, (int(trade_date), stock_code, signal_name, 1.0))
            except Exception as e:
                print(f"⚠️ 插入失败 {line}: {e}")

# === 提交并关闭 ===
conn.commit()
conn.close()

print("✅ 已将 txt 文件内容写入 t_stock_signal（重复已忽略）")
