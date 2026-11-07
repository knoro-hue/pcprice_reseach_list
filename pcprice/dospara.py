"""ドスパラ: 商品グリッド（全PC）＋詳細ページの仕様表（キャッシュ）から取得"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from .common import DESK, NOTE, Http, clean, finish, gb, mem_type, none_if_dash, num

MAKER = "ドスパラ"
BASE = "https://www.dospara.co.jp"
GRID = BASE + "/on/demandware.store/Sites-dospara-Site/ja_JP/Search-UpdateGrid?cgid=pc&start={start}&sz=60"
TILE = '<div class="product p-products-all-item-product" data-pid="'


def parse_grid(html_text: str) -> list[dict]:
    out = []
    for chunk in html_text.split(TILE)[1:]:
        pid = chunk.split('"', 1)[0]
        href = re.search(r'href="(/TC\d+/MC[^"]+\.html)"', chunk)
        name = re.search(r'product__name__text">\s*(?:<a[^>]*>)?(.*?)</', chunk, flags=re.S)
        price = re.search(r'product__number">([\d,]+)<', chunk)
        spec = {clean(k): clean(v) for k, v in re.findall(
            r'spec__item">(.*?)</th>\s*<td class="p-products-all-item-product__spec__text">(.*?)</td>', chunk, flags=re.S)}
        if not (href and price):
            continue
        out.append({"id": pid, "path": href.group(1), "name": clean(name.group(1)) if name else "",
                    "price": num(price.group(1)), "spec": spec})
    return out


def parse_detail(html_text: str) -> dict:
    i = html_text.find("製品仕様［基本構成］")
    if i < 0:
        return {}
    j = html_text.find("</table>", i)
    rows = re.findall(r"<tr><th>(.*?)</th><td>(.*?)</td></tr>", html_text[i:j], flags=re.S)
    spec = {}
    for k, v in rows:
        k = clean(k)
        if k and k not in spec:
            spec[k] = clean(v)
    storage = ""
    for k in ("SSD", "ストレージ", "SSD/HDD", "ハードディスク/SSD", "ハードディスク"):
        if none_if_dash(spec.get(k, "")) and "無し" not in spec.get(k, ""):
            storage = spec[k]
            break
    is_note = "バッテリー" in spec or re.search(r"インチ", spec.get("ディスプレイ", "")) is not None
    return {
        "kind": NOTE if is_note else DESK,
        "cpu": spec.get("CPU", ""), "gpu": spec.get("グラフィック機能", spec.get("グラフィックス", "")),
        "mem": spec.get("メモリ", ""), "storage": storage,
        "cooler": none_if_dash(spec.get("CPUファン", spec.get("CPUクーラー", ""))),
        "psu": spec.get("電源", ""),
    }


def collect(cache: dict, today: str, log=print, max_detail: int = 1500) -> tuple[list, dict, list]:
    http = Http(wait=0.5)
    tiles, seen, start = [], set(), 0
    while start < 6000:
        page = parse_grid(http.get(GRID.format(start=start)))
        new = [t for t in page if t["id"] not in seen]
        if not new:
            break
        for t in new:
            seen.add(t["id"])
        tiles += new
        start += 60
    warnings = []

    # 詳細（クーラー・電源・メモリ規格・区分）は新規分だけ取得し、30日ごとに少しずつ更新
    need = [t for t in tiles if t["id"] not in cache]
    stale = sorted((t for t in tiles if t["id"] in cache and cache[t["id"]].get("fetched", "") < _days_ago(today, 30)),
                   key=lambda t: cache[t["id"]].get("fetched", ""))[:100]
    todo = (need + stale)[:max_detail]

    def fetch(t):
        try:
            d = parse_detail(http.get(BASE + t["path"]))
            if d:
                d["fetched"] = today
            return t["id"], d
        except RuntimeError as e:
            return t["id"], {"error": str(e)}

    with ThreadPoolExecutor(2) as ex:
        for pid, d in ex.map(fetch, todo):
            if d and "error" not in d:
                cache[pid] = d
    left = len([t for t in tiles if t["id"] not in cache])
    if left:
        warnings.append(f"{MAKER}: 詳細仕様が未取得の商品 {left} 件（次回以降に取得）")

    rows = []
    for t in tiles:
        d = cache.get(t["id"], {})
        sp = t["spec"]
        mem_src = d.get("mem") or sp.get("メモリ", "")
        st = d.get("storage") or sp.get("ストレージ", "")
        kind = d.get("kind") or (NOTE if "Laptop" in sp.get("グラフィックス", "") else DESK)
        rows.append(finish({
            "maker": MAKER, "id": t["id"], "kind": kind, "name": t["name"], "price": t["price"],
            "cpu": sp.get("CPU") or d.get("cpu", ""), "gpu": sp.get("グラフィックス") or d.get("gpu", ""),
            "mem": re.split(r"\s*\((?:LP)?DDR", mem_src)[0] if mem_src else "",
            "mem_type": mem_type(mem_src) or mem_type(sp.get("メモリ", "")),
            "storage1": st, "storage_cap": _cap(st),
            "cooler": d.get("cooler", ""), "psu": d.get("psu", ""),
            "url": BASE + t["path"],
        }))
    totals = {k: sum(1 for r in rows if r["kind"] == k) for k in (DESK, NOTE)}
    log(f"{MAKER}: {len(rows)}件（詳細取得 {len(todo)}件 / 通信 {http.count}回）")
    return rows, totals, warnings


# 優先表示するドスパラの特集カテゴリ（ページ上の掲載順を保持）
FEATURED = [
    ("TC30", "GALLERIA デスク"),
    ("TC143", "GALLERIA ノート"),
    ("general_desk", "THIRDWAVE デスク"),
    ("general_note", "THIRDWAVE ノート"),
]


def featured(log=print) -> dict:
    """特集カテゴリごとの掲載商品ID（サイトの並び順）"""
    http = Http(wait=0.5)
    out = {}
    for cgid, label in FEATURED:
        ids, seen, start = [], set(), 0
        while start < 6000:
            page = parse_grid(http.get(GRID.replace("cgid=pc", f"cgid={cgid}").format(start=start)))
            new = [t["id"] for t in page if t["id"] not in seen]
            if not new:
                break
            seen.update(new)
            ids += new
            start += 60
        out[cgid] = {"label": label, "url": f"{BASE}/{cgid}", "ids": ids}
        log(f"{MAKER} 特集 {cgid}: {len(ids)}件")
    return out


def _cap(s: str) -> str:
    g = gb(s)
    if g is None:
        return ""
    return f"{g // 1000 if g % 1000 == 0 else g / 1000:g}TB" if g >= 1000 else f"{g}GB"


def _days_ago(today: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(today) - timedelta(days=n)).isoformat()
