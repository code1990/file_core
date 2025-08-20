import time
import sqlite3
import pymysql

DB_PATH = r"../stock.db"  # SQLite 数据库文件

# ===== MySQL 连接：启用服务端游标（流式）=====
mysql_conn = pymysql.connect(
    host="localhost",
    user="root",
    password="123456",
    database="ry-vue",
    charset="utf8mb4",
    cursorclass=pymysql.cursors.SSCursor,   # 关键：服务端游标
)
mysql_cur = mysql_conn.cursor()

# ===== SQLite 连接 =====
sqlite_conn = sqlite3.connect(DB_PATH)
sqlite_cur = sqlite_conn.cursor()

# ——导入加速用的 PRAGMA（仅导入阶段使用）——
sqlite_cur.execute("PRAGMA journal_mode=OFF;")
sqlite_cur.execute("PRAGMA synchronous=OFF;")
sqlite_cur.execute("PRAGMA temp_store=MEMORY;")
sqlite_cur.execute("PRAGMA cache_size=-200000;")  # 约 200MB 缓存

# ===== 重建表（导入后再建索引）=====
sqlite_cur.executescript("""
DROP TABLE IF EXISTS t_stock_daily;
CREATE TABLE t_stock_daily (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_code TEXT,
  stock_name TEXT,
  trade_date INTEGER,
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  vol REAL,
  amount REAL,
  vol_rate REAL,
  percent REAL,
  changes REAL,
  pre_close REAL,
  remark TEXT DEFAULT ''
);
""")
sqlite_conn.commit()

# ===== 流式查询（不要 fetchall）=====
select_sql = """
SELECT stock_code, stock_name, trade_date, open, high, low, close,
       vol, amount, vol_rate, percent, changes, pre_close, remark
FROM t_stock_daily
ORDER BY stock_code, trade_date
"""
mysql_cur.execute(select_sql)

# ===== 分批搬运 =====
BATCH = 20000
insert_sql = """
INSERT INTO t_stock_daily (
  stock_code, stock_name, trade_date, open, high, low, close,
  vol, amount, vol_rate, percent, changes, pre_close, remark
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

moved = 0
t0 = time.time()
while True:
    chunk = mysql_cur.fetchmany(BATCH)   # 关键：分批取
    if not chunk:
        break

    sqlite_cur.execute("BEGIN;")
    sqlite_cur.executemany(insert_sql, chunk)
    sqlite_conn.commit()

    moved += len(chunk)
    if moved % (BATCH * 5) == 0:
        speed = moved / max(time.time() - t0, 1)
        print(f"已迁移 {moved:,} 行，约 {speed:,.0f} 行/秒")

# ===== 导入完成后再建索引（更快）=====
sqlite_cur.executescript("""
CREATE UNIQUE INDEX IF NOT EXISTS uniq_stock_trade ON t_stock_daily(stock_code, trade_date);
CREATE INDEX IF NOT EXISTS t_stock_daily_idx ON t_stock_daily(stock_code, trade_date);
""")
sqlite_conn.commit()

mysql_conn.close()
sqlite_conn.close()
print(f"✅ 完成，累计写入 {moved:,} 行，用时 {time.time()-t0:.1f}s")
