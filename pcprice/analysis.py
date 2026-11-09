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


# ===== 提案（ゲーム×FPS別、GPU別） =====
FPS_STEPS = [60, 120, 144, 240]
RES = ["1080p", "1440p", "4K"]


def _is_pro(r):
    return bool(re.search(r"\bPro\b", r.get("os") or "", re.I))


def _brief(r, fps=None, est=False):
    if not r:
        return None
    return {"maker": r["maker"], "name": r["name"], "price": r["price"], "cpu": r["cpu_key"], "gpu": r["gpu_key"],
            "mem": r.get("mem_gb"), "st": r.get("storage_gb"), "psu": _psu_w(r), "grade": _grade(r), "url": r.get("url"),
            "fps": fps, "est": est}


def _mem_block(ds: list, pk: list, mem: int) -> dict:
    """GPU内の指定メモリ容量での当社提案・パソコン工房比較（同CPUで突き合わせ）"""
    d = [r for r in ds if r.get("mem_gb") == mem]
    p = [r for r in pk if r.get("mem_gb") == mem]
    by = {}
    for r in d + p:
        c = by.setdefault(r["cpu_key"], {})
        if r["maker"] not in c or r["price"] < c[r["maker"]]["price"]:
            c[r["maker"]] = r
    pairs = [(k, v[DS], v[PK]) for k, v in by.items() if DS in v and PK in v]
    item = lambda k, a, b: {"cpu": k, "mem": mem, "ds": _brief(a), "pk": _brief(b), "diff": a["price"] - b["price"]}
    win = [item(*x) for x in sorted(pairs, key=lambda x: x[1]["price"] - x[2]["price"]) if x[1]["price"] < x[2]["price"]][:3]
    lose = [item(*x) for x in sorted(pairs, key=lambda x: x[2]["price"] - x[1]["price"]) if x[1]["price"] > x[2]["price"]][:3]
    return {"mem": mem, "ds_n": len(d), "pk_n": len(p), "ds_min": _brief(d[0]) if d else None, "pk_min": _brief(p[0]) if p else None,
            "diff": (d[0]["price"] - p[0]["price"]) if d and p else None, "pairs": len(pairs),
            "ds_win": sum(1 for _, a, b in pairs if a["price"] < b["price"]), "pk_win": sum(1 for _, a, b in pairs if a["price"] > b["price"]),
            "win": win, "lose": lose}


def recommend(rows: list, fps_data: dict | None, today: str) -> dict:
    """ゲーム×解像度×目標FPSごとのおすすめ（ドスパラ最安）と、GPUごとのおすすめ・パソコン工房との比較"""
    desk = [r for r in rows if r.get("kind") == "デスクトップ" and r.get("price") and r.get("gpu_key") and not _is_pro(r)
            and r["maker"] in (DS, PK) and not re.search(r"PRO|^RTX A\d|Ada|Quadro", r["gpu_key"])]
    models = (fps_data or {}).get("models") or {}
    games = (fps_data or {}).get("games") or []
    rec_fps = (fps_data or {}).get("rec") or {}
    # GPU別のFPS中央値（ドスパラ実測）→ パソコン工房やFPS未掲載モデルの推定に使う
    acc = {}
    for r in desk:
        m = models.get(r["id"].split("-")[0]) if r["maker"] == DS else None
        for g, arr in (m or {}).items():
            for i, v in enumerate(arr):
                if v is not None:
                    acc.setdefault((r["gpu_key"], g, i), []).append(v)
    med = {k: statistics.median(v) for k, v in acc.items()}

    def fps_of(r, g, i):
        m = models.get(r["id"].split("-")[0]) if r["maker"] == DS else None
        if m and g in m and m[g][i] is not None:
            return m[g][i], False
        v = med.get((r["gpu_key"], g, i))
        return (int(round(v / 5) * 5), True) if v is not None else (None, True)

    by_game = []
    for g in games:
        res_out = []
        for i, res in enumerate(RES):
            steps = []
            for t in FPS_STEPS:
                ok = [(r, *fps_of(r, g, i)) for r in desk]
                ok = [(r, f, e) for r, f, e in ok if f is not None and f >= t]
                ds = sorted((x for x in ok if x[0]["maker"] == DS), key=lambda x: x[0]["price"])
                pk = sorted((x for x in ok if x[0]["maker"] == PK), key=lambda x: x[0]["price"])
                d, p = (ds[0] if ds else None), (pk[0] if pk else None)
                alt = []  # 別GPUの次点（ドスパラ）
                seen = {d[0]["gpu_key"]} if d else set()
                for x in ds[1:]:
                    if x[0]["gpu_key"] not in seen:
                        seen.add(x[0]["gpu_key"])
                        alt.append(_brief(x[0], x[1], x[2]))
                    if len(alt) >= 2:
                        break
                steps.append({"fps": t, "ds": _brief(d[0], d[1], d[2]) if d else None,
                              "pk": _brief(p[0], p[1], p[2]) if p else None,
                              "diff": (d[0]["price"] - p[0]["price"]) if d and p else None,
                              "ds_count": len(ds), "alt": alt})
            res_out.append({"res": res, "steps": steps})
        by_game.append({"game": g, "rec": rec_fps.get(g), "res": res_out})

    # GPU別
    gpus = sorted({r["gpu_key"] for r in desk if r["maker"] == DS})
    by_gpu = []
    for gk in gpus:
        ds = sorted((r for r in desk if r["maker"] == DS and r["gpu_key"] == gk), key=lambda r: r["price"])
        pk = sorted((r for r in desk if r["maker"] == PK and r["gpu_key"] == gk), key=lambda r: r["price"])
        # 同じ CPU×メモリ での比較
        cmp = {}
        for r in ds + pk:
            k = (r["cpu_key"], r.get("mem_gb"))
            c = cmp.setdefault(k, {})
            if r["maker"] not in c or r["price"] < c[r["maker"]]["price"]:
                c[r["maker"]] = r
        pairs = [(k, v[DS], v[PK]) for k, v in cmp.items() if DS in v and PK in v]
        lose = sorted(pairs, key=lambda x: x[2]["price"] - x[1]["price"])[:3]
        win = sorted(pairs, key=lambda x: x[1]["price"] - x[2]["price"])[:3]
        f = lambda lst, sign: [{"cpu": k[0], "mem": k[1], "ds": _brief(d), "pk": _brief(p), "diff": d["price"] - p["price"]}
                               for k, d, p in lst if (d["price"] - p["price"]) * sign > 0]
        # 用途別の提案：最安・コスパ（同GPU内でメモリ16GB以上の最安）・上位CPU（同GPU内の最上位帯で最安）
        mid = next((r for r in ds if (r.get("mem_gb") or 0) >= 16), None)
        by_gpu.append({"gpu": gk, "ds_n": len(ds), "pk_n": len(pk),
                       "ds_min": _brief(ds[0]), "pk_min": _brief(pk[0]) if pk else None,
                       "diff": (ds[0]["price"] - pk[0]["price"]) if pk else None,
                       "ds_16": _brief(mid) if mid and mid is not ds[0] else None,
                       "pairs": len(pairs), "ds_win": sum(1 for _, d, p in pairs if d["price"] < p["price"]),
                       "pk_win": sum(1 for _, d, p in pairs if d["price"] > p["price"]),
                       "lose": f(lose, 1), "win": f(win, -1),
                       "mems": [_mem_block(ds, pk, m) for m in (16, 32)]})
    order = {g: i for i, g in enumerate(["内蔵GPU", "RTX 3050", "RTX 5050", "RTX 5060", "RTX 5060 Ti", "RTX 5070", "RTX 5070 Ti",
                                         "RTX 5080", "RTX 5090", "RX 9060", "RX 9060 XT", "RX 9070", "RX 9070 GRE", "RX 9070 XT"])}
    by_gpu = [x for x in by_gpu if not re.search(r"PRO|^RTX A\d|Ada|Quadro", x["gpu"])]
    # 業務用GPUはおすすめ対象から外す（ゲーム別も）
    by_gpu.sort(key=lambda x: (order.get(x["gpu"], 50), x["ds_min"]["price"]))
    return {"updated": today, "basis": "デスクトップ・Windows Pro除外。同じGPU・CPU・メモリで両社の最安モデルを比較",
            "by_game": by_game, "by_gpu": by_gpu}


PROMPT2 = """あなたはドスパラ（自社）の販売アドバイザーです。以下のJSONは、ゲーム×目標FPS別のおすすめ（自社最安と競合パソコン工房の最安）と、GPU別の自社・競合比較です。
販売担当者が接客で使える「総評」を日本語で5行以内の箇条書きで書いてください。数値はJSONから引用し、推測は「推測」と明記。見出しは不要。

JSON:
"""


def ai_comment(reco: dict, log=print) -> dict | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    slim = {"by_gpu": [{k: g[k] for k in ("gpu", "ds_n", "pk_n", "diff", "ds_win", "pk_win")} for g in reco["by_gpu"]],
            "by_game_1080p": [{"game": g["game"], "steps": [{"fps": s["fps"], "ds": (s["ds"] or {}).get("price"), "ds_gpu": (s["ds"] or {}).get("gpu"),
                                                          "pk": (s["pk"] or {}).get("price"), "diff": s["diff"]} for s in g["res"][0]["steps"]]}
                              for g in reco["by_game"]]}
    client = anthropic.Anthropic()
    try:
        res = client.beta.messages.create(
            model="claude-opus-5-5", max_tokens=16000, output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": PROMPT2 + json.dumps(slim, ensure_ascii=False)}],
        )
    except anthropic.APIConnectionError as e:
        log(f"AI総評: 接続エラー {e}")
        return None
    except anthropic.APIStatusError as e:
        log(f"AI総評: APIエラー {e.status_code} {e.message}")
        return None
    if res.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in res.content if b.type == "text").strip()
    return {"updated": reco["updated"], "model": res.model, "text": text} if text else None
