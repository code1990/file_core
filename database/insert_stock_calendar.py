import sqlite3

# === 配置 ===
db_file = "stock.db"  # SQLite 数据库文件
txt_file = "data/date.txt"  # 存放交易日的 txt 文件

# === 打开数据库 ===
conn = sqlite3.connect(db_file)
cur = conn.cursor()
# cur.execute("""
# CREATE TABLE IF NOT EXISTS t_stock_calendar (
#   trade_date INTEGER NOT NULL PRIMARY KEY,
#   is_open    INTEGER NOT NULL
# );
# """)
# === 读取 txt 文件并插入 ===
with open(txt_file, "r", encoding="utf-8") as f:
    for line in f:
        date_str = line.strip()
        if not date_str:
            continue  # 跳过空行
        try:
            cur.execute("""
                INSERT OR IGNORE INTO t_stock_calendar (trade_date, is_open)
                VALUES (?, ?)
            """, (int(date_str), 1))  # 1=开市日
        except ValueError:
            print(f"⚠️ 跳过非法日期: {date_str}")

# === 提交并关闭 ===
conn.commit()
conn.close()

print("✅ 已将 date.txt 写入到 t_stock_calendar（重复已自动忽略）")
