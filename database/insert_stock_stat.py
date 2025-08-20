import pymysql
import sqlite3
import pandas as pd

DB_PATH = r"../stock.db"  # SQLite 数据库文件

# MySQL 连接
mysql_conn = pymysql.connect(
    host="localhost",
    user="root",
    password="123456",
    database="ry-vue",
    charset="utf8mb4"
)

# SQLite3 连接
sqlite_conn = sqlite3.connect(DB_PATH)

# 读取 MySQL 数据
df = pd.read_sql("SELECT * FROM t_stock_stat", mysql_conn)

# 写入 SQLite3
df.to_sql("t_stock_stat", sqlite_conn, if_exists="append", index=False)

# 关闭连接
mysql_conn.close()
sqlite_conn.close()

print("✅ 迁移完成")
