"""3社（パソコン工房・ドスパラ・マウス）のPC価格を取得し、比較用データとExcelを出力する

出力:
  data/products.json      全商品（最新）
  data/summary_gpu.json   区分×GPU別の社別 機種数・最安・中央値
  data/summary_cpu.json   区分×CPU別の社別 機種数・最安・中央値
  data/same_spec.json     同じ区分×CPU×GPUを2社以上が出している組合せの最安比較
  data/changes.json       値下げ・値上げ・新登場・終売（直近60日）
  data/history.json       日別×社×区分の機種数・中央値
  data/status.json        社ごとの取得結果
  output/pc_price_latest.xlsx, output/pckoubou_pc_latest.xlsx
使い方: python run_all.py [--only pckoubou,dospara,mouse]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import traceback
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from pcprice import dospara, mouse, pckoubou
from pcprice.common import DESK, FIELDS, NOTE

JST = timezone(timedelta(hours=9))
DATA, OUT, CACHE = "data", "output", "data/cache"
MAKERS = [("pk", pckoubou), ("ds", dospara), ("ms", mouse)]
CODE = {m.MAKER: c for c, m in MAKERS}


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def med(v):
    return int(statistics.median(v)) if v else None


def summarize(rows, key):
    groups = {}
    for r in rows:
        if r.get("price"):
            groups.setdefault((r["kind"], r[key]), {}).setdefault(CODE[r["maker"]], []).append(r["price"])
    out = []
    for (kind, k), by in groups.items():
        row = {"kind": kind, "key": k, "total": sum(len(v) for v in by.values())}
        best = None
        for c, _ in MAKERS:
            v = by.get(c, [])
            row[f"{c}_n"], row[f"{c}_min"], row[f"{c}_med"] = len(v), (min(v) if v else None), med(v)
            if v and (best is None or min(v) < best[1]):
                best = (c, min(v))
        row["makers"] = sum(1 for c, _ in MAKERS if by.get(c))
        row["cheapest"] = {c: m.MAKER for c, m in MAKERS}[best[0]] if best else ""
        out.append(row)
    return sorted(out, key=lambda r: (r["kind"], -r["total"]))


def same_spec(rows):
    groups = {}
    for r in rows:
        if r.get("price") and r["gpu_key"]:
            g = groups.setdefault((r["kind"], r["cpu_key"], r["gpu_key"]), {})
            c = CODE[r["maker"]]
            if c not in g or r["price"] < g[c]["price"]:
                g[c] = r
    out = []
    for (kind, cpu, gpu), by in groups.items():
        if len(by) < 2:
            continue
        row = {"kind": kind, "cpu": cpu, "gpu": gpu, "spec": f"{cpu} × {gpu}"}
        for c, _ in MAKERS:
            r = by.get(c)
            row[f"{c}_min"] = r["price"] if r else None
            row[f"{c}_name"] = r["name"] if r else ""
            row[f"{c}_mem"] = (f"{r['mem_gb']}GB" if r and r.get("mem_gb") else "")
            row[f"{c}_url"] = r["url"] if r else ""
        prices = {c: r["price"] for c, r in by.items()}
        lo = min(prices, key=prices.get)
        row["cheapest"] = {c: m.MAKER for c, m in MAKERS}[lo]
        row["min"] = prices[lo]
        row["spread"] = max(prices.values()) - prices[lo]
        row["makers"] = len(by)
        out.append(row)
    return sorted(out, key=lambda r: (r["kind"], -r["makers"], r["min"]))


def diff(prev, cur, today):
    p = {(r["maker"], r["id"]): r for r in prev}
    c = {(r["maker"], r["id"]): r for r in cur}
    out = []
    for k, r in c.items():
        o = p.get(k)
        base = {"date": today, "maker": r["maker"], "id": r["id"], "kind": r["kind"], "name": r["name"],
                "gpu_key": r["gpu_key"], "cpu_key": r["cpu_key"], "url": r["url"]}
        if o is None:
            out.append({**base, "type": "新登場", "old": None, "new": r["price"], "delta": None})
        elif o.get("price") and r.get("price") and o["price"] != r["price"]:
            out.append({**base, "type": "値下げ" if r["price"] < o["price"] else "値上げ",
                        "old": o["price"], "new": r["price"], "delta": r["price"] - o["price"]})
    for k, o in p.items():
        if k not in c:
            out.append({"date": today, "maker": o["maker"], "id": o["id"], "kind": o["kind"], "name": o["name"],
                        "gpu_key": o.get("gpu_key", ""), "cpu_key": o.get("cpu_key", ""), "url": o.get("url", ""),
                        "type": "終売・掲載終了", "old": o.get("price"), "new": None, "delta": None})
    return out


def write_xlsx(path, rows, info, cols=None):
    cols = cols or FIELDS
    wb = Workbook()
    ws = wb.active
    ws.title = "PCスペック・価格一覧"
    ws.append([h for _, h in cols])
    for r in rows:
        ws.append([r.get(k) for k, _ in cols])
    widths = {"maker": 12, "id": 14, "kind": 11, "name": 40, "price": 13, "cpu": 30, "gpu": 32, "mem": 18,
              "mem_type": 14, "storage1": 26, "storage_cap": 10, "cooler": 26, "psu": 30, "url": 40}
    for i, (k, _) in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = widths.get(k, 14)
        if k == "price":
            for (c,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                c.number_format = "#,##0"
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(rows) + 1}"
    wi = wb.create_sheet("取得情報")
    for r in info:
        wi.append(r)
    wi.column_dimensions["A"].width, wi.column_dimensions["B"].width = 22, 100
    wb.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="pk,ds,ms")
    args = ap.parse_args()
    only = set(args.only.split(","))

    started = datetime.now(JST)
    today = started.date().isoformat()
    prev = load(f"{DATA}/products.json", [])
    rows, status, warnings = [], [], []

    for code, mod in MAKERS:
        old = [r for r in prev if r["maker"] == mod.MAKER]
        if code not in only:
            rows += old
            continue
        try:
            if mod is pckoubou:
                got, totals, warn = mod.collect()
            else:
                cache = load(f"{CACHE}/{code}_detail.json", {})
                got, totals, warn = mod.collect(cache, today)
                save(f"{CACHE}/{code}_detail.json", cache)
            if not got:
                raise RuntimeError("0件でした")
            rows += got
            warnings += warn
            status.append({"maker": mod.MAKER, "state": "取得成功", "fetched_at": started.strftime("%Y-%m-%d %H:%M"),
                           "desk": sum(1 for r in got if r["kind"] == DESK), "note": sum(1 for r in got if r["kind"] == NOTE),
                           "site_desk": totals.get(DESK), "site_note": totals.get(NOTE), "message": " / ".join(warn)})
        except Exception as e:  # noqa: BLE001 1社の失敗で全体を止めない
            traceback.print_exc()
            rows += old
            msg = f"{mod.MAKER}: 取得失敗のため前回データを表示（{e}）"
            warnings.append(msg)
            last = next((s for s in load(f"{DATA}/status.json", []) if s["maker"] == mod.MAKER), {})
            status.append({"maker": mod.MAKER, "state": "失敗（前回データ）", "fetched_at": last.get("fetched_at", ""),
                           "desk": sum(1 for r in old if r["kind"] == DESK), "note": sum(1 for r in old if r["kind"] == NOTE),
                           "site_desk": None, "site_note": None, "message": str(e)[:300]})

    # 同一社内の重複IDを除去 → 社・区分・価格順
    seen, uniq = set(), []
    for r in rows:
        k = (r["maker"], r["id"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    order = {m.MAKER: i for i, (_, m) in enumerate(MAKERS)}
    uniq.sort(key=lambda r: (order[r["maker"]], r["kind"] != DESK, r["price"] or 10**12, r["id"]))

    # 前日比（初回は比較しない）
    changes = load(f"{DATA}/changes.json", [])
    if prev:
        ok = {s["maker"] for s in status if s["state"] == "取得成功"}
        new_changes = [c for c in diff([r for r in prev if r["maker"] in ok], [r for r in uniq if r["maker"] in ok], today)]
        changes = [c for c in changes if c["date"] != today] + new_changes
    keep = (started.date() - timedelta(days=60)).isoformat()
    changes = sorted((c for c in changes if c["date"] >= keep), key=lambda c: (c["date"], c["maker"], c["type"]), reverse=True)

    history = [h for h in load(f"{DATA}/history.json", []) if h["date"] != today]
    for _, mod in MAKERS:
        for kind in (DESK, NOTE):
            v = [r["price"] for r in uniq if r["maker"] == mod.MAKER and r["kind"] == kind and r.get("price")]
            history.append({"date": today, "maker": mod.MAKER, "kind": kind, "count": len(v),
                            "median": med(v), "min": min(v) if v else None})
    history.sort(key=lambda h: (h["date"], h["maker"], h["kind"]))

    save(f"{DATA}/products.json", uniq)
    save(f"{DATA}/summary_gpu.json", summarize(uniq, "gpu_key"))
    save(f"{DATA}/summary_cpu.json", summarize(uniq, "cpu_key"))
    save(f"{DATA}/same_spec.json", same_spec(uniq))
    save(f"{DATA}/changes.json", changes)
    save(f"{DATA}/history.json", history)
    save(f"{DATA}/status.json", status)

    info = [["取得日時", started.strftime("%Y-%m-%d %H:%M") + " JST"]]
    info += [[s["maker"], f"{s['state']}　デスクトップ {s['desk']}件 / ノート {s['note']}件"] for s in status]
    info += [["注意", " / ".join(warnings) or "なし"],
             ["販売価格(税込)", "BTOモデルは基本構成（最低）価格。マウスはセール価格を優先"]]
    os.makedirs(OUT, exist_ok=True)
    write_xlsx(f"{OUT}/pc_price_latest.xlsx", uniq, info)
    write_xlsx(f"{OUT}/pc_price_{started:%Y%m%d_%H%M}.xlsx", uniq, info)
    pk_cols = [f for f in FIELDS if f[0] not in ("maker", "url")]
    write_xlsx(f"{OUT}/pckoubou_pc_latest.xlsx", [r for r in uniq if r["maker"] == pckoubou.MAKER], info, pk_cols)

    today_changes = [c for c in changes if c["date"] == today]
    lines = [f"## PC価格調査 {started:%Y-%m-%d %H:%M} JST", ""]
    lines += [f"- {s['maker']}: {s['state']} デスク {s['desk']} / ノート {s['note']}" for s in status]
    lines += [f"- 本日の変動: " + " / ".join(f"{t} {sum(1 for c in today_changes if c['type'] == t)}件"
                                            for t in ("値下げ", "値上げ", "新登場", "終売・掲載終了"))]
    lines += [f"- ⚠ {w}" for w in warnings]
    print("\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    for w in warnings:
        print("::warning::" + w)
    return 0 if any(s["state"] == "取得成功" for s in status) else 1


if __name__ == "__main__":
    sys.exit(main())
