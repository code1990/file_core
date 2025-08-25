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

def get_info_20240427140():
    total = 300000
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

    for i in range(250000, total):
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
# cd /mydata/model/mydata/data
# zip -r code2.zip code2

# if __name__ == "__main__":
#     print("获取到的token:", get_token())
