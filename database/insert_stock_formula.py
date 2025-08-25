# pip install PyExecJS
import execjs
import os
import os
import time
import random
import requests
import json
def get_token(script_path=r"./wencai.js"):
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"脚本文件不存在: {script_path}")

    with open(script_path, "r", encoding="utf-8") as f:
        js_code = f.read()

    ctx = execjs.compile(js_code)
    return ctx.call("fn")

import sqlite3
import json
from typing import Dict, Any

DB_PATH = "formula.db"

def init_db(db_path: str = DB_PATH):
    """初始化 SQLite 表"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS t_formula (
        id INTEGER PRIMARY KEY,
        name TEXT,
        source_code TEXT,
        label_name TEXT,
        uploader_name TEXT,
        upload_time TEXT,
        instruction TEXT,
        hot_val INTEGER,
        click_times INTEGER,
        discuss_number INTEGER,
        avg_star REAL,
        market_list TEXT,
        extra_json TEXT
    )
    """)
    conn.commit()
    conn.close()


def save_formulas(response: Dict[str, Any], db_path: str = DB_PATH):
    """
    保存接口返回的数据到 SQLite
    :param response: 接口返回的 JSON（dict 格式）
    :param db_path: SQLite 文件路径
    """
    if not response or "data" not in response:
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    count = 0
    for item in response.get("data", []):
        cur.execute("""
        INSERT OR REPLACE INTO t_formula
        (id, name, source_code, label_name, uploader_name, upload_time,
         instruction, hot_val, click_times, discuss_number, avg_star,
         market_list, extra_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.get("id"),
            item.get("name"),
            item.get("sourceCode"),
            item.get("labelName"),
            item.get("uploaderName"),
            item.get("uploadTime"),
            item.get("instruction"),
            item.get("hotVal"),
            item.get("clickTimes"),
            item.get("discussNumber"),
            item.get("avgStar"),
            json.dumps(item.get("marketList"), ensure_ascii=False),
            json.dumps(item, ensure_ascii=False)
        ))
        count += 1

    conn.commit()
    conn.close()
    return count

def get_info_20240427140():
    total = 325417
    save_dir = r"../data/code2"
    os.makedirs(save_dir, exist_ok=True)

    token = str(get_token())   # 获取一次 token
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Cookie": f"v={token}",
        "Hexin-V": token,
        "Referer": "http://poi.10jqka.com.cn/front/html/"
    }

    for i in range(300000, total):
        try:
            file_path = os.path.join(save_dir, f"{i}.txt")
            if os.path.exists(file_path):
                continue

            url = f"http://poi.10jqka.com.cn/api/technical/formula/info/?id={i}"

            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            json_str = resp.text
            json_obj = json.loads(json_str)

            print(i)
            print(json_obj)

            # 保存完整 JSON
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json_str)

            # ✅ 如果只想保存源码，可以启用这段
            # data = json_obj.get("data", [])
            # if data:
            #     code = data[0].get("sourceCode")
            #     if code:
            #         with open(file_path, "w", encoding="utf-8") as f:
            #             f.write(code)
            # else:
            #     with open(file_path, "w", encoding="utf-8") as f:
            #         f.write("")

            # 随机等待 1-2 秒，防止被封
            time.sleep(1 + random.random())

        except Exception as e:
            print("请求失败:", i, e)
            # 短暂等待后继续
            time.sleep(2)


if __name__ == "__main__":
    get_info_20240427140()

# if __name__ == "__main__":
#     print("获取到的token:", get_token())
