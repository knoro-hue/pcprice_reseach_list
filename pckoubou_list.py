"""パソコン工房 PCスペック・価格一覧 取得（GitHub Actions / ローカル共通）

公式通販の検索API(npsearch.php)からデスクトップ・ノートPCの全商品を取得し、Excelに出力する。
検索APIはオフセット120件以降を返さないため、価格帯で120件以下になるまで二分割してから各帯を全ページ取得する。

使い方:  python pckoubou_list.py --out output
必要:    pip install openpyxl
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BASE = os.environ.get("PCK_BASE", "https://www.pc-koubou.jp")
API = BASE + "/search/npsearch.php"
PAGE_SIZE = 30      # APIの1回あたり上限
MAX_DEPTH = 120     # APIが返せるオフセットの上限
PRICE_MAX = 99_999_999
WORKERS = 2         # 同時取得数
WAIT = 0.3          # 1接続あたりの取得間隔(秒)
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

KINDS = {"desk": ("デスクトップ", "デスク"), "note": ("ノート", "ノート")}

# 出力列: (見出し, 列幅, 取得元)  取得元が "spec:" で始まるものは narrow91 のスペック項目名
COLUMNS = [
    ("商品ID", 10, "id"),
    ("区分", 11, "kind"),
    ("型番", 34, "name"),
    ("販売価格(税込)", 13, "price"),
    ("CPU型番", 30, "spec:CPU型番"),
    ("グラフィックス", 30, "spec:グラフィックス"),
    ("メモリ容量", 16, "spec:メモリ容量"),
    ("メモリ規格", 22, "spec:メモリ規格"),
    ("1stストレージ", 22, "spec:1stストレージ"),
    ("ストレージ容量", 12, "spec:1stストレージ容量"),
    ("クーラー", 22, "spec:CPUクーラー"),
    ("電源", 32, "spec:電源"),
]


def clean(v) -> str:
    if v is None:
        return ""
    t = str(v)
    for _ in range(2):  # &amp;lt; のような二重エスケープにも対応
        t = html.unescape(t)
    t = re.sub(r"<[^>]*>", " ", t)
    return re.sub(r"[\s　]+", " ", t).strip()


def num(v):
    if v in (None, ""):
        return None
    try:
        n = float(str(v).replace(",", ""))
    except ValueError:
        return None
    return int(n) if n.is_integer() else n


def spec_map(item: dict) -> dict:
    """narrow91 = 「項目名##値」をタブ区切りで並べた全スペック"""
    out = {}
    for part in str(item.get("narrow91") or "").split("\t"):
        if "##" not in part:
            continue
        k, v = part.split("##", 1)
        k = clean(k)
        if k and k not in out:
            out[k] = clean(v)
    return out


def to_row(item: dict, kind: str) -> list:
    spec = spec_map(item)
    src = {
        "id": num(item.get("product_id") or item.get("itemid")),
        "kind": KINDS[kind][0],
        "name": clean(item.get("name") or item.get("title")),
        "price": num(item.get("price02_min_inctax")) or num(item.get("number33")),
    }
    return [spec.get(key[5:], "") if key.startswith("spec:") else src[key] for _, _, key in COLUMNS]


def fetch_page(kind: str, lo=None, hi=None, o=0, limit=PAGE_SIZE) -> tuple[dict, list]:
    q = {"fmt": "json", "s2b": "通常", "limit": str(limit), "o": str(o), "sort": "Itemid", "s86b": KINDS[kind][1]}
    if lo is not None:
        q["n33l"] = str(lo)
    if hi is not None:
        q["n33h"] = str(hi)
    url = API + "?" + urllib.parse.urlencode(q)
    last = None
    for attempt in range(1, 5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as res:
                text = res.read().decode("utf-8", "replace")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                raise RuntimeError("JSONではない応答（アクセス制限またはサイト仕様変更の可能性）")
            result = (data.get("kotohaco") or {}).get("result") or {}
            info = result.get("info") or {}
            if info.get("status") != 0:
                raise RuntimeError("検索APIの応答が異常です")
            return info, result.get("items") or []
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * attempt)
    rng = f"（{lo}〜{hi}円帯 {o + 1}件目〜）" if lo is not None else ""
    raise RuntimeError(f"{KINDS[kind][0]}{rng}の取得に失敗: {last}")


def collect(kind: str, warnings: list) -> tuple[list, int]:
    total = int(fetch_page(kind, limit=1)[0].get("hitnum", 0))
    items, tasks = [], []
    probes = [(0, PRICE_MAX)]
    while probes:  # 価格帯を120件以下になるまで二分割（帯の1ページ目はここで保存）
        lo, hi = probes.pop()
        info, page = fetch_page(kind, lo, hi)
        h = int(info.get("hitnum", 0))
        if h == 0:
            pass
        elif h <= MAX_DEPTH or lo >= hi:
            items += page
            tasks += [(lo, hi, o) for o in range(PAGE_SIZE, min(h, MAX_DEPTH), PAGE_SIZE)]
            if h > MAX_DEPTH:
                warnings.append(f"{KINDS[kind][0]} {lo:,}円の商品が{h}件あり、120件を超える分は取得できません")
        else:
            mid = (lo + hi) // 2
            probes += [(mid + 1, hi), (lo, mid)]  # 安い帯から処理
        time.sleep(WAIT)

    def work(t):
        lo, hi, o = t
        page = fetch_page(kind, lo, hi, o)[1]
        time.sleep(WAIT)
        return page

    with ThreadPoolExecutor(WORKERS) as ex:
        for page in ex.map(work, tasks):
            items += page
    return [to_row(it, kind) for it in items], total


def write_xlsx(path: str, rows: list, info_rows: list) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "PCスペック・価格一覧"
    ws.append([c[0] for c in COLUMNS])
    for r in rows:
        ws.append(r)
    head_fill = PatternFill("solid", fgColor="005E4A")
    for i, (_, width, key) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
        if key == "price":
            for (c,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                c.number_format = "#,##0"
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(rows) + 1}"

    wi = wb.create_sheet("取得情報")
    for r in info_rows:
        wi.append(r)
    wi.column_dimensions["A"].width = 22
    wi.column_dimensions["B"].width = 90
    for (c,) in wi.iter_rows(min_col=1, max_col=1):
        c.font = Font(bold=True)
    wb.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="output", help="出力フォルダ")
    ap.add_argument("--kinds", default="desk,note", help="desk / note / desk,note")
    args = ap.parse_args()

    started = datetime.now(JST)
    kinds = [k for k in args.kinds.split(",") if k in KINDS]
    rows, warnings, summary = [], [], []
    for kind in kinds:
        got, total = collect(kind, warnings)
        rows += got
        summary.append((kind, total))

    # 重複除去（同一商品IDは1行）→ 区分・価格順
    seen, uniq = set(), []
    for r in rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        uniq.append(r)
    order = {v[0]: i for i, v in enumerate(KINDS.values())}
    uniq.sort(key=lambda r: (order.get(r[1], 9), r[3] if r[3] is not None else 10**12, r[0] or 0))

    for kind, total in summary:
        n = sum(1 for r in uniq if r[1] == KINDS[kind][0])
        if n != total:
            warnings.append(f"{KINDS[kind][0]}: 取得 {n} 件 / サイト表示 {total} 件（差 {total - n}）")

    finished = datetime.now(JST)
    info_rows = [
        ["取得元", "パソコン工房 公式通販（" + BASE + "）"],
        ["取得開始", started.strftime("%Y-%m-%d %H:%M:%S")],
        ["取得終了", finished.strftime("%Y-%m-%d %H:%M:%S")],
        ["件数", " / ".join(f"{KINDS[k][0]} {sum(1 for r in uniq if r[1] == KINDS[k][0]):,}件（サイト {t:,}件）" for k, t in summary)],
        ["販売価格(税込)", "BTOモデルは最低構成の価格"],
        ["注意", "なし" if not warnings else " / ".join(warnings)],
    ]

    os.makedirs(args.out, exist_ok=True)
    stamp = started.strftime("%Y%m%d_%H%M")
    dated = os.path.join(args.out, f"pckoubou_pc_{stamp}.xlsx")
    latest = os.path.join(args.out, "pckoubou_pc_latest.xlsx")
    write_xlsx(dated, uniq, info_rows)
    write_xlsx(latest, uniq, info_rows)
    print(f"出力: {dated}（{len(uniq):,}件）")
    for w in warnings:
        print("::warning::" + w)

    gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_summary:
        with open(gh_summary, "a", encoding="utf-8") as f:
            f.write(f"## パソコン工房 PC価格一覧\n\n- 取得日時: {started:%Y-%m-%d %H:%M} JST\n- {info_rows[3][1]}\n")
            for w in warnings:
                f.write(f"- ⚠ {w}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print("::error::" + str(e))
        sys.exit(1)
