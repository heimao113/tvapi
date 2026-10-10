#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoonTV 视频源聚合管线
拉取上游源配置 → 解码(Base58/明文)→ 合并去重 → 探测分类接口(测活/成人判定/内容分类)
→ 产出 完整版 / 人版(无成人) / 18禁版(仅成人) JSON + Excel 清单
"""
import base64
import concurrent.futures as cf
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 8
WORKERS = 24

# 上游源列表:fetch 顺序即合并优先级,先到先得(重名/同接口)
SOURCES = [
    {"label": "仓库1-增强版", "url": "https://raw.githubusercontent.com/pqguanyinli/moontv_shipinyuan/main/moontv_shipinyuan_1.json", "encoding": "base58"},
    {"label": "仓库2-基础版", "url": "https://raw.githubusercontent.com/pqguanyinli/moontv_shipinyuan/main/moontv_shipinyuan_2.json", "encoding": "base58"},
    {"label": "仓库3",        "url": "https://raw.githubusercontent.com/pqguanyinli/moontv_shipinyuan/main/moontv_shipinyuan_3.json", "encoding": "base58"},
    {"label": "cmliu配置",    "url": "https://raw.githubusercontent.com/cmliu/cmliu/refs/heads/main/tvapi_config_json", "encoding": "base58"},
    {"label": "sp696",        "url": "https://sp.696.de5.net?format=0&source=full", "encoding": "plain"},
]

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# ---- 成人判定 ----
# 域名命中:上游已明确打 AV- 标或域名众所周知
ADULT_DOMAINS = {
    "155api.com", "bwzyz.com", "lbapiby.com", "jkunzyapi.com", "souavzy.vip",
    "lbapi9.com", "aosikazy.com", "naixxzy.com", "slapibf.com", "xrbsp.com",
    "apiyutu.com", "fhapi9.com", "kxgav.com", "jingpinx.com", "msnii.com",
    "apilsbzy1.com", "sexnguon.com", "maozyapi.com", "apilj.com", "gdlsp.com",
    "shayuapi.com", "pgxdy.com", "91md.me", "gayapi.com", "xingba111.com",
    "hsckzy888.com", "xxibaozyw.com",
}
# 名称命中(兜底,探测失败时用)
ADULT_NAME_KW = ["av", "黄色", "成人", "伦理", "性吧", "杏吧", "色猫", "色嗨",
                 "麻豆", "淫", "番号", "奶香", "香奶", "老色逼", "gay", "sex",
                 "jkun", "玉兔", "鲨鱼", "辣椒", "美少女", "白嫖", "精品资源"]
# 分类名命中(实测,最权威)
ADULT_CAT_RE = re.compile(
    r"无码|有码|传媒|伦理|麻豆|巨乳|人妻|熟女|偷拍|自拍|少妇|口爆|颜射|中出|"
    r"三级|激情|艳舞|约炮|援交|情色|色情|性爱|做爱|中文字幕|福利|写真|"
    r"抖阴|蜜桃|萝莉|御姐|极品|骚|女优|风俗|探花"
)
# 判定为成人站:强命中 ≥3 条且占分类总数 ≥30%(正规站常带单个"伦理片/福利"栏目,不能只看单命中)
ADULT_MIN_HITS = 3
ADULT_MIN_RATIO = 0.30

session = requests.Session()
session.headers.update({"User-Agent": UA})
session.verify = False


def b58decode(s: str) -> bytes:
    s = s.strip()
    n = 0
    for c in s:
        n = n * 58 + B58_ALPHABET.index(c)
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + b


def b58encode(b: bytes) -> str:
    """标准 bs58(Bitcoin 字母表),与 MoonTV 订阅解码兼容;前导 0x00 → '1'"""
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58_ALPHABET[r] + out
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + out


def fetch_source(src) -> dict:
    r = session.get(src["url"], timeout=30)
    r.raise_for_status()
    text = r.text.strip().lstrip("\ufeff")
    if src["encoding"] == "base58":
        return json.loads(b58decode(text).decode("utf-8"))
    return json.loads(text)


def norm_api(api: str) -> str:
    """归一化接口地址:host + path + query(代理源靠 query 区分,不能丢)"""
    if not api:
        return ""
    p = urllib.parse.urlsplit(api if "//" in api else "http://" + api)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + p.path.rstrip("/") + (("?" + p.query) if p.query else "")


def merge_all() -> tuple:
    merged, order, lives = {}, [], {}
    for src in SOURCES:
        try:
            data = fetch_source(src)
        except Exception as e:
            print(f"[拉取失败] {src['label']}: {e}", file=sys.stderr)
            continue
        for key, v in data.get("api_site", {}).items():
            api = (v.get("api") or "").strip()
            nid = norm_api(api)
            if not nid:
                nid = "noapi::" + key
            if nid not in merged:
                merged[nid] = {
                    "key": key, "api": api, "name": v.get("name", ""),
                    "detail": v.get("detail", ""), "from": [src["label"]],
                }
                order.append(nid)
            else:
                rec = merged[nid]
                rec["from"].append(src["label"])
                if len(v.get("name", "")) > len(rec["name"]):
                    rec["name"] = v.get("name", "")
                if not rec["detail"] and v.get("detail"):
                    rec["detail"] = v["detail"]
        for k, v in data.get("lives", {}).items():
            lives.setdefault(k, v)
        print(f"[拉取完成] {src['label']}: {len(data.get('api_site', {}))} 站, lives={len(data.get('lives', {}))}")
    print(f"[合并] 去重后 {len(order)} 个唯一接口")
    return merged, order, lives


def probe(nid: str, rec: dict) -> dict:
    """实测分类接口,返回 {alive, cats, adult, tags, ms}"""
    api = rec["api"].rstrip("/")
    res = {"alive": False, "cats": [], "probe_adult": None, "mixed": False, "hits": [],
           "tags": "", "err": "", "ms": None}
    for suffix in ("?ac=list", "?ac=videolist"):
        try:
            import time
            t0 = time.time()
            r = session.get(api + suffix, timeout=TIMEOUT)
            ms = int((time.time() - t0) * 1000)
            if r.status_code != 200:
                res["err"] = f"HTTP {r.status_code}"
                continue
            res["alive"] = True
            res["ms"] = ms
            try:
                j = r.json()
            except ValueError:
                res["err"] = "非JSON响应"
                continue
            cats = [c.get("type_name", "") for c in (j.get("class") or []) if c.get("type_name")]
            if cats or j.get("list"):
                res["cats"] = cats
                strong = [c for c in cats if ADULT_CAT_RE.search(c)]
                res["hits"] = strong
                res["probe_adult"] = len(strong) >= ADULT_MIN_HITS and len(strong) / len(cats) >= ADULT_MIN_RATIO
                res["mixed"] = bool(strong) and not res["probe_adult"]
                tags = set()
                joined = " ".join(cats)
                if res["probe_adult"]:
                    tags.add("18禁")
                if re.search(r"短剧|剧场|ysz", joined):
                    tags.add("短剧")
                if re.search(r"动漫|番剧|动画", joined):
                    tags.add("动漫")
                if re.search(r"电影|剧集|电视剧|连续剧|综艺|影视", joined):
                    tags.add("影视")
                res["tags"] = "/".join(sorted(tags)) or ("18禁" if res["probe_adult"] else "综合")
                break
            res["err"] = "空分类"
        except requests.RequestException as e:
            res["err"] = type(e).__name__
    return res


def classify(nid: str, rec: dict, probe_res: dict) -> dict:
    host = urllib.parse.urlsplit(rec["api"]).netloc.lower().removeprefix("www.")
    name = rec["name"]
    domain_adult = any(host == d or host.endswith("." + d) for d in ADULT_DOMAINS)
    name_adult = ("🔞" in name) or ((not name.startswith("TV-")) and any(kw in name.lower() for kw in ADULT_NAME_KW))

    if probe_res["probe_adult"] is True:
        adult, why = True, "分类实测"
    elif probe_res["probe_adult"] is False and probe_res["cats"]:
        # 探测明确为普通站,但上游明确打了成人标记的仍以上游为准
        if domain_adult or "🔞" in name:
            adult, why = True, "上游标记"
        else:
            adult, why = False, "分类实测"
    elif domain_adult or "🔞" in name:
        adult, why = True, "域名/标记"
    elif name_adult:
        adult, why = True, "名称关键词"
    else:
        adult, why = False, "无法探测→按普通处理"

    if not probe_res["alive"]:
        status = "失活"
    elif not probe_res["cats"]:
        status = "存活/非标准"
    else:
        status = "存活"
    return {"adult": adult, "why": why, "status": status,
            "mixed": (not adult) and bool(probe_res.get("hits"))}


def keyify(nid: str, rec: dict, used: set) -> str:
    key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", rec["key"]).strip("_") or "src"
    base, i = key, 1
    while key in used:
        i += 1
        key = f"{base}{i}"
    used.add(key)
    return key


def emit(merged, order, results, lives):
    os.makedirs(OUT, exist_ok=True)
    full, clean, adult = {"cache_time": 7200, "api_site": {}}, {"cache_time": 7200, "api_site": {}}, {"cache_time": 7200, "api_site": {}}
    used = set()
    rows = []
    for nid in order:
        rec, pres = merged[nid], results[nid]
        cls = classify(nid, rec, pres)
        entry = {"name": rec["name"], "api": rec["api"]}
        if rec["detail"]:
            entry["detail"] = rec["detail"]
        k = keyify(nid, rec, used)
        full["api_site"][k] = entry
        (adult if cls["adult"] else clean)["api_site"][k] = entry
        rows.append({
            "key": k, "名称": rec["name"], "接口": rec["api"], "域名": rec["detail"] or "",
            "是否成人": "是" if cls["adult"] else "否", "判定依据": cls["why"],
            "状态": cls["status"], "响应ms": pres.get("ms") if pres.get("ms") is not None else "-",
            "内容标签": pres["tags"] or "-",
            "含成人栏目": "是(少量)" if cls["mixed"] else "-",
            "成人栏目数": len(pres.get("hits", [])),
            "分类名": " | ".join(pres["cats"][:20]), "来源": ",".join(dict.fromkeys(rec["from"])),
        })

    if lives:
        full["lives"] = lives
        clean["lives"] = lives  # 直播源(B站/YY/抖音)为普通内容
    for obj, fname in [(full, "moontv_完整版.json"), (clean, "moontv_人版.json"), (adult, "moontv_18禁版.json")]:
        with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        # MoonTV 订阅要求 Base58 编码:同名 _b58.json,单行无换行(尾随换行会导致 bs58 解码报错)
        with open(os.path.join(OUT, fname.replace(".json", "_b58.json")), "w", encoding="utf-8") as f:
            payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            f.write(b58encode(payload))

    # CSV + Excel
    cols = list(rows[0].keys()) if rows else []
    with open(os.path.join(OUT, "源清单.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
        wb = Workbook()
        ws = wb.active
        ws.title = "源清单"
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="4472C4")
        for r in rows:
            ws.append([r[c] for c in cols])
            if r["是否成人"] == "是":
                for cell in ws[ws.max_row]:
                    cell.fill = PatternFill("solid", fgColor="FFC7CE")
        for i, c in enumerate(cols, 1):
            width = max(len(str(r[c] or "")) for r in rows + [{c: c}])
            ws.column_dimensions[get_column_letter(i)].width = min(max(width * 1.6, 8), 60)
        # 第二个 sheet:仅成人
        ws2 = wb.create_sheet("18禁源")
        ws2.append(cols)
        for r in rows:
            if r["是否成人"] == "是":
                ws2.append([r[c] for c in cols])
        wb.save(os.path.join(OUT, "源清单.xlsx"))
    except ImportError:
        print("[警告] openpyxl 未安装,仅输出 CSV", file=sys.stderr)

    n_a = sum(1 for r in rows if r["是否成人"] == "是")
    alive = sum(1 for r in rows if r["状态"] == "存活")
    print(f"[产出] 完整版 {len(full['api_site'])} | 人版 {len(clean['api_site'])} | 18禁版 {len(adult['api_site'])} (实测成人 {n_a}, 存活 {alive}/{len(rows)})")


def main():
    merged, order, lives = merge_all()
    results = {}
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(probe, nid, merged[nid]): nid for nid in order}
        for fu in cf.as_completed(futs):
            nid = futs[fu]
            try:
                results[nid] = fu.result()
            except Exception as e:
                results[nid] = {"alive": False, "cats": [], "probe_adult": None, "mixed": False,
                                "hits": [], "tags": "", "err": str(e), "ms": None}
    emit(merged, order, results, lives)
    print("[完成]")


if __name__ == "__main__":
    main()
