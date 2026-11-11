"""パソコン工房: 検索API(npsearch.php)から全PCを取得

APIはオフセット120件以降を返さないため、価格帯で120件以下になるまで二分割してから各帯を全ページ取得する。
"""
from __future__ import annotations

import json
import urllib.parse

from .common import DESK, NOTE, Http, clean, finish, num

MAKER = "パソコン工房"
BASE = "https://www.pc-koubou.jp"
API = BASE + "/search/npsearch.php"
PAGE, DEPTH, PRICE_MAX = 30, 120, 99_999_999
KINDS = {DESK: "デスク", NOTE: "ノート"}


def _page(http: Http, kind: str, lo=None, hi=None, o=0, limit=PAGE):
    q = {"fmt": "json", "s2b": "通常", "limit": str(limit), "o": str(o), "sort": "Itemid", "s86b": KINDS[kind]}
    if lo is not None:
        q["n33l"], q["n33h"] = str(lo), str(hi)
    text = http.get(API + "?" + urllib.parse.urlencode(q))
    try:
        res = json.loads(text)["kotohaco"]["result"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("パソコン工房: JSONではない応答（アクセス制限または仕様変更の可能性）") from e
    if (res.get("info") or {}).get("status") != 0:
        raise RuntimeError("パソコン工房: 検索APIの応答が異常です")
    return res["info"], res.get("items") or []


def _spec(item) -> dict:
    out = {}
    for part in str(item.get("narrow91") or "").split("\t"):
        if "##" in part:
            k, v = part.split("##", 1)
            k = clean(k)
            if k and k not in out:
                out[k] = clean(v)
    return out


def _row(item, kind) -> dict:
    s = _spec(item)
    url = item.get("url") or ""
    return finish({
        "maker": MAKER,
        "id": str(item.get("product_id") or item.get("itemid") or ""),
        "kind": kind,
        "name": clean(item.get("name") or item.get("title")),
        "price": num(item.get("price02_min_inctax")) or num(item.get("number33")),
        "cpu": s.get("CPU型番", ""), "gpu": s.get("グラフィックス", ""), "os": s.get("OS", ""),
        "delivery": "短納期対象" if "短納期" in str(item.get("product_tags") or "") else "", "delivery_days": None,
        "_deliv_id": str(item.get("deliv_date_id") or ""),
        "mem": s.get("メモリ容量", ""), "mem_type": s.get("メモリ規格", ""),
        "storage1": s.get("1stストレージ", ""), "storage_cap": s.get("1stストレージ容量", ""),
        "cooler": s.get("CPUクーラー", ""), "psu": s.get("電源", ""),
        "url": (BASE + url) if url.startswith("/") else url,
    })


def _deliv_text(html_text: str) -> str:
    """商品ページの「出荷予定 2～7日後 (最大1週間)」を取り出す"""
    import re
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_text))
    m = re.search(r"出荷予定\s+(.+?)\s+(?:出荷元|商品到着目安)", t)
    return clean(m.group(1)) if m else ""


def _deliv_days(text: str):
    """「2～7日後」→ 7（最大日数）。読めなければ None"""
    import re
    if "当日" in text:
        return 0
    if "翌日" in text or "翌営業日" in text:
        return 1
    m = re.search(r"(\d+)\s*[～~〜-]\s*(\d+)\s*日", text) or re.search(r"(\d+)\s*日", text)
    if not m:
        w = re.search(r"(\d+)\s*週間", text)
        return int(w.group(1)) * 7 if w else None
    return int(m.group(m.lastindex))


def apply_delivery(rows: list, http: "Http", cache: dict, log=print) -> None:
    """deliv_date_id（出荷予定の種類）ごとに商品ページを1回だけ開いて文言を覚え、全商品に付ける"""
    for r in rows:
        did = r.pop("_deliv_id", "")
        if not did:
            continue
        if did not in cache:
            try:
                cache[did] = _deliv_text(http.get(r["url"]))
                log(f"{MAKER} 出荷予定 id={did}: {cache[did] or '（読めず）'}")
            except RuntimeError:
                cache[did] = ""
        txt = cache.get(did, "")
        if txt:
            r["delivery"] = (txt if "出荷" in txt else f"出荷{txt}") + ("・短納期" if r.get("delivery") == "短納期対象" else "")
            r["delivery_days"] = _deliv_days(txt)


def collect(log=print, deliv_cache: dict | None = None) -> tuple[list, dict, list]:
    http = Http(wait=15)  # robots.txt の Crawl-delay: 15 に従う
    rows, totals, warnings = [], {}, []
    for kind in (DESK, NOTE):
        totals[kind] = int(_page(http, kind, limit=1)[0].get("hitnum", 0))
        items, tasks, probes = [], [], [(0, PRICE_MAX)]
        while probes:
            lo, hi = probes.pop()
            info, page = _page(http, kind, lo, hi)
            h = int(info.get("hitnum", 0))
            if h == 0:
                continue
            if h <= DEPTH or lo >= hi:
                items += page
                tasks += [(lo, hi, o) for o in range(PAGE, min(h, DEPTH), PAGE)]
                if h > DEPTH:
                    warnings.append(f"{MAKER} {kind} {lo:,}円の商品が{h}件あり、120件を超える分は取得不可")
            else:
                mid = (lo + hi) // 2
                probes += [(mid + 1, hi), (lo, mid)]
        for t in tasks:  # 間隔を守るため1本ずつ
            items += _page(http, kind, t[0], t[1], t[2])[1]
        rows += [_row(it, kind) for it in items]
        log(f"{MAKER} {kind}: {len(items)}件（サイト {totals[kind]}件 / 累計通信 {http.count}回）")
    apply_delivery(rows, http, deliv_cache if deliv_cache is not None else {}, log)
    return rows, totals, warnings
