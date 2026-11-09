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


# ドスパラの注力ページ。ページ内の「おすすめランキング」と注力枠（CPU×GPU表・ラインアップ・特集枠）の掲載モデルだけを使う
FOCUS_PAGES = [
    ("TC30", "ゲーミングデスク"),
    ("TC143", "ゲーミングノート"),
    ("general_desk", "個人向けデスク"),
    ("general_note", "スタンダードノート"),
]
RANK_API = BASE + "/s/dospara/api/getCategoryRanking?cid={cid}"
RANK_LABEL = {
    "TC1030": "GALLERIA Xシリーズ ランキング", "TC1031": "GALLERIA Fシリーズ ランキング",
    "TC1032": "GALLERIA Eシリーズ ランキング", "TC973": "THIRDWAVE ゲーミング ランキング",
    "TC143": "ゲーミングノート ランキング", "general_desk": "個人向けデスク ランキング",
    "general_note": "スタンダードノート ランキング",
}
# 注力枠として扱わない見出し（キャンペーン・検索フォーム・価格帯から選ぶ等）
SKIP_HEAD = re.compile(r"キャンペーン|新着|条件を設定|予算|価格から|^$")


def _mc(s: str) -> str | None:
    m = re.search(r"MC\d+", s)
    return m.group(0) if m else None


def parse_focus(page: str, label: str, html_text: str, ranking) -> list[dict]:
    """注力ページ1枚から掲載グループを作る。ranking(cid) は API 応答の productInfoList を返す関数"""
    events = []
    for m in re.finditer(r"<(h[1-4])[^>]*>(.*?)</\1>", html_text, flags=re.S):
        events.append((m.start(), "head", re.sub(r"\s+", " ", clean(m.group(2))).strip()))
    for m in re.finditer(r'get_ranking_data[^>]*data-categoryname="([^"]+)"', html_text):
        events.append((m.start(), "rank", m.group(1)))
    for m in re.finditer(r"MC\d+", html_text):
        events.append((m.start(), "mc", m.group(0)))
    events.sort()
    groups, seen_rank, head = [], set(), ""
    static = {}
    for _, typ, val in events:
        if typ == "head":
            head = val
        elif typ == "rank" and val not in seen_rank:
            seen_rank.add(val)
            ids = []
            for it in ranking(val):
                mc = _mc(it.get("url", ""))
                if mc and mc not in ids:
                    ids.append(mc)
            if ids:
                groups.append({"page": page, "pageLabel": label, "key": f"rk:{val}", "type": "ranking",
                               "label": RANK_LABEL.get(val, f"{head} ランキング"), "ids": ids})
        elif typ == "mc" and not SKIP_HEAD.search(head) and "ランキング" not in head:
            g = static.get(head)
            if g is None:
                short = "CPU×GPU おすすめ表" if "CPU・GPUの組み合わせ" in head else head[:40]
                g = static[head] = {"page": page, "pageLabel": label, "key": f"sec:{page}:{head}", "type": "focus",
                                    "label": short, "ids": []}
                groups.append(g)
            if val not in g["ids"]:
                g["ids"].append(val)
    return [g for g in groups if g["ids"]]


def focus(today: str, log=print) -> dict:
    """注力ページのランキング・注力枠の掲載モデル（ページ上の並び順）"""
    import json
    http = Http(wait=0.5)
    cache: dict = {}

    def ranking(cid):
        if cid not in cache:
            try:
                d = json.loads(http.get(RANK_API.format(cid=cid)))
                cache[cid] = d.get("productInfoList") or [] if d.get("returnCode") == "000000" else []
            except Exception:  # noqa: BLE001
                cache[cid] = []
        return cache[cid]

    groups, keys = [], set()
    for page, label in FOCUS_PAGES:
        for g in parse_focus(page, label, http.get(f"{BASE}/{page}"), ranking):
            if g["key"] not in keys:  # 同じランキングが複数ページにある場合は最初の1回だけ
                keys.add(g["key"])
                groups.append(g)
        log(f"{MAKER} 注力 {page}: 累計 {len(groups)} グループ")
    return {"updated": today, "groups": groups}


def fps(today: str, sample_url: str, log=print) -> dict | None:
    """ドスパラ詳細ページの「ゲーム性能」データ（モデル別FPS）。
    詳細ページの hidden input にある ul_fpsdate.json（全モデル分）と game_fps_score.json（推奨FPS）を取得し、
    {models: {MC番号: {ゲーム: [1080p, 1440p, 4K]}}} に圧縮する（画質は「最高」＝サイト表示と同じ）"""
    import json
    http = Http(wait=0.5)
    page = http.get(sample_url)
    m_data = re.search(r'id="fpsDataFile" value="([^"]+)"', page)
    m_score = re.search(r'id="fpsScoreFile" value="([^"]+)"', page)
    if not m_data:
        return None
    raw = json.loads(http.get(BASE + m_data.group(1)).lstrip("\ufeff"))
    rec = json.loads(http.get(BASE + m_score.group(1)).lstrip("\ufeff")) if m_score else {}
    res = ["1080p/FHD", "1440p/WQHD", "2160p/4K"]

    def n(v):
        m = re.match(r"\s*(\d+)", str(v))
        return int(m.group(1)) if m else None
    models, games = {}, []
    for mc, v in raw.items():
        out = {}
        for g, q in ((v or {}).get("game_list") or {}).items():
            if g not in games:
                games.append(g)
            arr = [n((q.get("最高") or {}).get(r)) for r in res]
            if any(x is not None for x in arr):
                out[g] = arr
        if out:
            models[mc] = out
    log(f"{MAKER} FPS: {len(models)}モデル")
    return {"updated": today, "quality": "最高", "res": ["1080p", "1440p", "4K"], "games": games,
            "rec": {g: (rec.get(g) or {}).get("1080p/FHD") for g in games}, "models": models}


def _cap(s: str) -> str:
    g = gb(s)
    if g is None:
        return ""
    return f"{g // 1000 if g % 1000 == 0 else g / 1000:g}TB" if g >= 1000 else f"{g}GB"


def _days_ago(today: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(today) - timedelta(days=n)).isoformat()
