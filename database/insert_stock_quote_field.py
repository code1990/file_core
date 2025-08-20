import time
import requests
import math

def get_stock_total_count():
    """获取股票总数"""
    url = "https://quotedata.cnfin.com/quote/v1/sort"
    params = {
        "sort_field_name": "px_change_rate",
        "sort_type": "undefined",
        "start_pos": 0,
        "data_count": 1,
        "en_hq_type_code": "SS.ESA.M,SZ.ESA.M,SZ.ESA.SMSE,SZ.ESA.GEM,SS.KSH,SZ.ESA.SMSE",
        "fields": "null",
        "request_sort_count": 1,
        "localDate": str(int(time.time() * 1000))
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    js = resp.json()
    return js["data"]["sort"]["sort_result_count"]

def fetch_fields(start_pos=0, page_size=100):
    """抓取某一页，返回字段列表"""
    url = "https://quotedata.cnfin.com/quote/v1/sort"
    params = {
        "sort_field_name": "px_change_rate",
        "sort_type": 1,
        "start_pos": start_pos,
        "data_count": page_size,
        "en_hq_type_code": "SS.ESA.M,SZ.ESA.M,SZ.ESA.SMSE,SZ.ESA.GEM,SS.KSH,SZ.ESA.SMSE",
        "fields": "null",
        "localDate": str(int(time.time() * 1000))
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    js = resp.json()
    return js["data"]["sort"]["fields"]

def fetch_all_fields(page_size=100):
    """分页抓取，收集所有 fields"""
    total = get_stock_total_count()
    total_pages = math.ceil(total / page_size)
    print(f"总股票数: {total}, 总页数: {total_pages}")

    all_fields = set()

    for i in range(total_pages):
        start_pos = i * page_size
        fields = fetch_fields(start_pos, page_size)
        all_fields.update(fields)
        print(f"✅ 已收集第 {i+1}/{total_pages} 页字段，总计 {len(all_fields)} 个字段")

    # 写入 txt
    with open("./data/stock_fields.txt", "w", encoding="utf-8") as f:
        for field in sorted(all_fields):
            f.write(field + "\n")

    print(f"🎉 已收集所有字段（共 {len(all_fields)} 个），写入 fields.txt")

if __name__ == "__main__":
    fetch_all_fields(page_size=100)
