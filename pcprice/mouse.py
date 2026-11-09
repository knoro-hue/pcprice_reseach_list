"""マウスコンピューター: カテゴリ一覧（デスクトップ/ノート）＋詳細ページの仕様表（キャッシュ）から取得

対象ブランド: mouse / NEXTGEAR / G-Tune / DAIV / MousePro（公式ストアの形状別カテゴリ全件）
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from .common import DESK, NOTE, Http, clean, finish, gb, mem_type, none_if_dash, num
from .dospara import _cap, _days_ago

MAKER = "マウス"
BASE = "https://www.mouse-jp.co.jp"
CATS = {DESK: "/store/r/ra2010000", NOTE: "/store/r/ra2020000"}
ITEM = "block-goods-list-4CD--item js-enhanced-ecommerce-item"


def parse_list(html_text: str) -> list[dict]:
    out = []
    for chunk in html_text.split(ITEM)[1:]:
        href = re.search(r'href="(/store/g/g([^/"]+)/)"', chunk)
        if not href:
            continue
        name = re.search(r'item-name">(.*?)</p>', chunk, flags=re.S)
        code = re.search(r'item_code2">\[([^\]]+)\]', chunk)
        price_block = chunk.split('goods-price-after', 1)[1] if 'goods-price-after' in chunk else chunk
        price = re.search(r'goods-price-zeikomi"><span class="goods-price-value">([\d,]+)<', price_block)
        specs = [clean(s) for s in re.findall(r'item-spec-item-text">(.*?)</span>', chunk, flags=re.S)]
        out.append({"id": (code.group(1) if code else href.group(2)).upper(), "path": href.group(1),
                    "name": clean(name.group(1)) if name else "", "price": num(price.group(1)) if price else None,
                    "specs": specs})
    return out


def parse_detail(html_text: str) -> dict:
    spec = {}
    for tr in re.findall(r"<tr>(.*?)</tr>", html_text, flags=re.S):
        ths = re.findall(r"<th[^>]*>(.*?)</th>", tr, flags=re.S)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)
        if not (ths and tds):
            continue
        k = clean(re.sub(r'<a[^>]*notes_link[^>]*>.*?</a>', "", ths[-1], flags=re.S))
        if k and k not in spec:
            spec[k] = clean(tds[0])
    if "CPU" not in spec:
        return {}
    storage = ""
    for k in ("M.2 SSD", "SSD", "ハードディスク"):
        if none_if_dash(spec.get(k, "")):
            storage = spec[k]
            break
    return {
        "cpu": spec.get("CPU", ""), "gpu": spec.get("グラフィックス", ""),
        "mem": spec.get("メモリ標準容量", ""), "mem_type": spec.get("メモリ動作", ""),
        "storage": storage, "cooler": none_if_dash(spec.get("CPUクーラー", "")),
        "psu": spec.get("電源", ""),
    }


def _pick(specs: list, pat: str) -> str:
    for s in specs:
        if re.search(pat, s, flags=re.I):
            return s
    return ""


def collect(cache: dict, today: str, log=print, max_detail: int = 1500) -> tuple[list, dict, list]:
    http = Http(wait=0.5)
    items, totals, warnings = [], {}, []
    for kind, path in CATS.items():
        first = http.get(BASE + path + "/")
        m = re.search(r"(\d[\d,]*)\s*件", re.sub(r"<[^>]+>", " ", first))
        totals[kind] = num(m.group(1)) if m else None
        pages = sorted({int(p) for p in re.findall(re.escape(path) + r"_p(\d+)/", first)} | {1})
        got = parse_list(first)
        for p in range(2, (max(pages) if pages else 1) + 1):
            got += parse_list(http.get(f"{BASE}{path}_p{p}/"))
        seen = set()
        for g in got:
            if g["id"] not in seen:
                seen.add(g["id"])
                g["kind"] = kind
                items.append(g)
        if totals[kind] and len(seen) != totals[kind]:
            warnings.append(f"{MAKER} {kind}: 一覧 {len(seen)}件 / サイト表示 {totals[kind]}件")

    need = [t for t in items if t["id"] not in cache]
    stale = sorted((t for t in items if t["id"] in cache and cache[t["id"]].get("fetched", "") < _days_ago(today, 30)),
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
    left = len([t for t in items if t["id"] not in cache])
    if left:
        warnings.append(f"{MAKER}: 詳細仕様が未取得の商品 {left} 件（次回以降に取得）")

    rows = []
    for t in items:
        d, sp = cache.get(t["id"], {}), t["specs"]
        st = d.get("storage") or _pick(sp, r"NVMe|SSD|HDD")
        rows.append(finish({
            "maker": MAKER, "id": t["id"], "kind": t["kind"], "name": t["name"], "price": t["price"],
            "cpu": d.get("cpu") or _pick(sp, r"Core|Ryzen|Xeon|Celeron|プロセッサ"),
            "gpu": d.get("gpu") or _pick(sp, r"GeForce|Radeon|Arc|グラフィックス|RTX"),
            "os": _pick(sp, r"Windows|Chrome|Linux|OS[な無]し|OSなし"),
            "mem": d.get("mem") or _pick(sp, r"GB \(.*(?:チャネル|×)"),
            "mem_type": mem_type(d.get("mem_type", "")) or d.get("mem_type", ""),
            "storage1": st, "storage_cap": _cap(st),
            "cooler": d.get("cooler", ""), "psu": d.get("psu", ""),
            "url": BASE + t["path"],
        }))
    log(f"{MAKER}: {len(rows)}件（詳細取得 {len(todo)}件 / 通信 {http.count}回）")
    return rows, totals, warnings
