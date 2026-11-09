"""ドスパラ vs パソコン工房 の比較統計と AI 分析（Claude）

compare(): 同じ 区分×CPU×GPU×メモリ（Windows Pro 除外）で両社の最安モデルを突き合わせた統計
ai_summary(): 統計を Claude に渡し「ドスパラが有利な点／不利な点」を文章化（ANTHROPIC_API_KEY があるときだけ）
"""
from __future__ import annotations

import json
import os
import re
import statistics

DS, PK = "ドスパラ", "パソコン工房"
BANDS = [(0, 150000, "〜15万"), (150000, 200000, "15〜20万"), (200000, 250000, "20〜25万"), (250000, 300000, "25〜30万"),
         (300000, 400000, "30〜40万"), (400000, 10**12, "40万〜")]


def _psu_w(p):
    m = re.search(r"(\d{2,4})\s*W", p.get("psu") or "")
    return int(m.group(1)) if m else None


def _grade(p):
    m = re.search(r"(TITANIUM|PLATINUM|GOLD|SILVER|BRONZE)", (p.get("psu") or "").upper())
    return m.group(1) if m else ""


def _band(price):
    return next(lab for lo, hi, lab in BANDS if lo <= price < hi)


def compare(rows: list, today: str) -> dict:
    ok = [r for r in rows if r.get("price") and r.get("gpu_key") and r.get("mem_gb") and "PRO" not in (r.get("os") or "").upper().replace("PROCESSOR", "")]
    best = {}
    for r in ok:
        if r["maker"] not in (DS, PK):
            continue
        k = (r["kind"], r["cpu_key"], r["gpu_key"], r["mem_gb"])
        cur = best.setdefault(k, {})
        if r["maker"] not in cur or r["price"] < cur[r["maker"]]["price"]:
            cur[r["maker"]] = r
    pairs, only = [], {DS: 0, PK: 0}
    for k, v in best.items():
        if DS in v and PK in v:
            d, p = v[DS], v[PK]
            pairs.append({"kind": k[0], "cpu": k[1], "gpu": k[2], "mem": k[3], "ds": d["price"], "pk": p["price"],
                          "diff": d["price"] - p["price"], "ds_name": d["name"], "pk_name": p["name"],
                          "ds_st": d.get("storage_gb"), "pk_st": p.get("storage_gb"),
                          "ds_psu": _psu_w(d), "pk_psu": _psu_w(p), "ds_grade": _grade(d), "pk_grade": _grade(p),
                          "ds_url": d.get("url"), "pk_url": p.get("url")})
        else:
            only[DS if DS in v else PK] += 1 if (DS in v or PK in v) else 0

    def summ(ps):
        if not ps:
            return None
        diffs = [x["diff"] for x in ps]
        return {"n": len(ps), "ds_cheaper": sum(d < 0 for d in diffs), "pk_cheaper": sum(d > 0 for d in diffs),
                "tie": sum(d == 0 for d in diffs), "median_diff": int(statistics.median(diffs)),
                "median_pct": round(statistics.median(x["diff"] / x["pk"] * 100 for x in ps), 1)}

    def by(keyf):
        g = {}
        for x in pairs:
            g.setdefault(keyf(x), []).append(x)
        return {k: summ(v) for k, v in g.items()}

    spec = {"st_more": sum(1 for x in pairs if x["ds_st"] and x["pk_st"] and x["ds_st"] > x["pk_st"]),
            "st_less": sum(1 for x in pairs if x["ds_st"] and x["pk_st"] and x["ds_st"] < x["pk_st"]),
            "psu_more": sum(1 for x in pairs if x["ds_psu"] and x["pk_psu"] and x["ds_psu"] > x["pk_psu"]),
            "psu_less": sum(1 for x in pairs if x["ds_psu"] and x["pk_psu"] and x["ds_psu"] < x["pk_psu"])}
    order = {"": 0, "BRONZE": 1, "SILVER": 2, "GOLD": 3, "PLATINUM": 4, "TITANIUM": 5}
    spec["grade_better"] = sum(1 for x in pairs if order[x["ds_grade"]] > order[x["pk_grade"]] and x["pk_grade"])
    spec["grade_worse"] = sum(1 for x in pairs if order[x["ds_grade"]] < order[x["pk_grade"]] and x["ds_grade"])
    count = {kind: {m: sum(1 for r in ok if r["maker"] == m and r["kind"] == kind) for m in (DS, PK)} for kind in ("デスクトップ", "ノート")}
    s = sorted(pairs, key=lambda x: x["diff"])
    return {"updated": today, "basis": "同じ区分×CPU×GPU×メモリで両社の最安モデルを比較（Windows Pro除外）。差＝ドスパラ−パソコン工房",
            "total": summ(pairs), "by_kind": by(lambda x: x["kind"]),
            "by_gpu": {k: v for k, v in by(lambda x: f'{x["kind"]}|{x["gpu"]}').items() if v["n"] >= 2},
            "by_band": by(lambda x: f'{x["kind"]}|{_band(x["pk"])}'),
            "by_mem": by(lambda x: f'{x["kind"]}|{x["mem"]}GB'),
            "spec": spec, "only": only, "count": count,
            "ds_best": s[:10], "ds_worst": s[::-1][:10]}


PROMPT = """あなたはBTOパソコン市場の価格アナリストです。ドスパラ（自社）の商品企画・販売担当者向けに、競合パソコン工房との比較結果をまとめてください。

以下のJSONは、同じ「区分×CPU×GPU×メモリ」構成について両社の最安モデルを突き合わせた統計です（Windows Pro搭載モデルは除外）。
diff・median_diff は「ドスパラ − パソコン工房」の金額（マイナス＝ドスパラが安い）。by_gpu / by_band / by_mem のキーは「区分|値」。
ds_best はドスパラが最も安い構成、ds_worst はドスパラが最も高い構成。spec はストレージ容量・電源容量・80PLUSグレードの比較件数。

出力は次の見出しで、日本語・箇条書き中心・簡潔に。数値は必ずJSONから引用し、推測は「推測」と明記すること。
## 総評（3行以内）
## ドスパラが有利な点
## ドスパラが不利な点
## 価格以外の差（ストレージ・電源など）
## 打ち手の提案（3つまで）

JSON:
"""


def ai_summary(stats: dict, log=print) -> dict | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    client = anthropic.Anthropic()
    payload = json.dumps(stats, ensure_ascii=False)
    try:
        res = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": PROMPT + payload}],
        )
    except anthropic.APIConnectionError as e:
        log(f"AI分析: 接続エラー {e}")
        return None
    except anthropic.APIStatusError as e:
        log(f"AI分析: APIエラー {e.status_code} {e.message}")
        return None
    if res.stop_reason == "refusal":
        log("AI分析: 応答が拒否されました")
        return None
    text = "".join(b.text for b in res.content if b.type == "text").strip()
    return {"updated": stats["updated"], "model": res.model, "text": text} if text else None
