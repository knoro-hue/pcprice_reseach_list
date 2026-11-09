"""パソコン工房: 検索API(npsearch.php)から全PCを取得

APIはオフセット120件以降を返さないため、価格帯で120件以下になるまで二分割してから各帯を全ページ取得する。
"""
from __future__ import annotations

import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

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
        "mem": s.get("メモリ容量", ""), "mem_type": s.get("メモリ規格", ""),
        "storage1": s.get("1stストレージ", ""), "storage_cap": s.get("1stストレージ容量", ""),
        "cooler": s.get("CPUクーラー", ""), "psu": s.get("電源", ""),
        "url": (BASE + url) if url.startswith("/") else url,
    })


def collect(log=print) -> tuple[list, dict, list]:
    http = Http(wait=0.3)
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
        with ThreadPoolExecutor(2) as ex:
            for page in ex.map(lambda t: _page(http, kind, t[0], t[1], t[2])[1], tasks):
                items += page
        rows += [_row(it, kind) for it in items]
        log(f"{MAKER} {kind}: {len(items)}件（サイト {totals[kind]}件）")
    return rows, totals, warnings
