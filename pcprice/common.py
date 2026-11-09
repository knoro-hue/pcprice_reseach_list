"""共通処理: HTTP取得・文字列整形・スペック正規化"""
from __future__ import annotations

import html
import re
import threading
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# 出力列（Excel・ダッシュボード共通）。key → 見出し
FIELDS = [
    ("maker", "メーカー"), ("id", "商品ID"), ("kind", "区分"), ("name", "型番"),
    ("price", "販売価格(税込)"), ("cpu", "CPU型番"), ("gpu", "グラフィックス"),
    ("os", "OS"), ("mem", "メモリ容量"), ("mem_type", "メモリ規格"), ("storage1", "1stストレージ"),
    ("storage_cap", "ストレージ容量"), ("cooler", "クーラー"), ("psu", "電源"), ("url", "商品URL"),
]
DESK, NOTE = "デスクトップ", "ノート"


class Http:
    """サイトごとに間隔を空けて取得する簡易クライアント（リトライ付き）"""

    def __init__(self, wait: float = 0.5, timeout: int = 40):
        self.wait = wait
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last = 0.0
        self.count = 0

    def get(self, url: str, encoding: str = "utf-8") -> str:
        last = None
        for attempt in range(1, 5):
            with self._lock:
                gap = self.wait - (time.time() - self._last)
                if gap > 0:
                    time.sleep(gap)
                self._last = time.time()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.5"})
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    self.count += 1
                    return res.read().decode(encoding, "replace")
            except Exception as e:  # noqa: BLE001
                last = e
                if getattr(e, "code", None) == 404:
                    break
                time.sleep(2 * attempt)
        raise RuntimeError(f"取得失敗 {url}: {last}")


def clean(v) -> str:
    if v is None:
        return ""
    t = str(v)
    t = re.sub(r"<br\s*/?>", " / ", t, flags=re.I)
    for _ in range(2):
        t = html.unescape(t)
    t = re.sub(r"<[^>]*>", " ", t)
    t = t.replace("™", "").replace("®", "").replace("(R)", "").replace("(TM)", "")
    return re.sub(r"[\s　]+", " ", t).strip()


def num(v):
    if v in (None, ""):
        return None
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(v))
    if not m:
        return None
    try:
        n = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return int(n) if n.is_integer() else n


def none_if_dash(s: str) -> str:
    s = (s or "").strip()
    return "" if s in ("ー", "-", "－", "―", "なし", "無し") else s


# ---------- 正規化（3社を同じ軸で比べるためのキー） ----------

def _z(s: str) -> str:
    s = clean(s)
    s = s.translate(str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－＋",
                                  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-+"))
    return re.sub(r"プロセッサー?|インテル|Intel|AMD|NVIDIA", " ", s, flags=re.I)


def cpu_key(s: str) -> str:
    t = re.sub(r"\s+", " ", _z(s))
    pats = [
        (r"Core\s*Ultra\s*([3579])\s*(\d{3}[A-Z]{0,3})(\s*Plus)?", lambda m: f"Core Ultra {m[1]} {m[2]}{' Plus' if m[3] else ''}"),
        (r"Core\s*i([3579])\s*-?\s*(\d{4,5}[A-Z]{0,3})", lambda m: f"Core i{m[1]}-{m[2]}"),
        (r"Core\s*([3579])\s*(\d{3}[A-Z]{0,3})\b", lambda m: f"Core {m[1]} {m[2]}"),
        (r"Ryzen\s*AI\s*(Max\+?|[579])\s*(?:PRO\s*)?(HX\s*)?(\d{3}[A-Z]{0,3})", lambda m: f"Ryzen AI {m[1]} {'HX ' if m[2] else ''}{m[3]}"),
        (r"Ryzen\s*Threadripper\s*(PRO\s*)?(\d{4}[A-Z0-9]{0,3})", lambda m: f"Ryzen Threadripper {'PRO ' if m[1] else ''}{m[2]}"),
        (r"Ryzen\s*([3579])\s*(PRO\s*)?(\d{4}[A-Z0-9]{0,4})", lambda m: f"Ryzen {m[1]} {'PRO ' if m[2] else ''}{m[3]}"),
        (r"Xeon\s*(w\d-\d{4,5}[A-Z]?|[A-Za-z]+\s*\d{4}[A-Z]?|\d{4}[A-Z]?)", lambda m: f"Xeon {m[1]}"),
        (r"(Celeron|Pentium|Athlon)\s*(?:Gold|Silver)?\s*([A-Z]?\d{3,4}[A-Z]{0,2})", lambda m: f"{m[1]} {m[2]}"),
        (r"\b(N\d{2,3})\b", lambda m: m[1]),
    ]
    for p, f in pats:
        m = re.search(p, t, flags=re.I)
        if m:
            return re.sub(r"\s+", " ", f(m)).strip()
    return t.strip()[:40]


def _suf(s: str) -> str:
    return re.sub(r"\s+", " ", s.upper().replace("TI", "Ti")).strip()


def gpu_key(s: str, kind: str) -> str:
    t = re.sub(r"\s+", " ", _z(s))
    laptop = kind == NOTE or re.search(r"Laptop", t, flags=re.I)
    pats = [
        (r"RTX\s*PRO\s*(\d{4})", lambda m: f"RTX PRO {m[1]}"),
        (r"RTX\s*A(\d{3,4})", lambda m: f"RTX A{m[1]}"),
        (r"RTX\s*(\d{4})\s*Ada", lambda m: f"RTX {m[1]} Ada"),
        (r"RTX\s*(\d{4})\s*(Ti\s*SUPER|Ti|SUPER)?", lambda m: f"RTX {m[1]}{' ' + _suf(m[2]) if m[2] else ''}"),
        (r"GTX\s*(\d{4})\s*(Ti|SUPER)?", lambda m: f"GTX {m[1]}{' ' + _suf(m[2]) if m[2] else ''}"),
        (r"RX\s*(\d{4})\s*(XTX|XT|GRE)?", lambda m: f"RX {m[1]}{' ' + m[2].upper() if m[2] else ''}"),
        (r"Arc\s*(Pro\s*)?([AB]\d{2,3})", lambda m: f"Arc {'Pro ' if m[1] else ''}{m[2].upper()}"),
        (r"Radeon\s*(AI\s*)?PRO\s*([RW]\d{4})", lambda m: f"Radeon {'AI ' if m[1] else ''}PRO {m[2].upper()}"),
    ]
    for p, f in pats:
        m = re.search(p, t, flags=re.I)
        if m:
            k = re.sub(r"\s+", " ", f(m)).strip()
            return k + (" Laptop" if laptop and not k.startswith(("RTX PRO", "RTX A")) else "")
    return "内蔵GPU"


def gb(s: str):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB)", _z(s), flags=re.I)
    if not m:
        return None
    v = float(m[1]) * (1000 if m[2].upper() == "TB" else 1)
    return int(v) if v.is_integer() else v


def mem_type(s: str) -> str:
    m = re.search(r"(LPDDR\d[X]?|DDR\d)(?:[-‐]?(\d{4,5}))?", _z(s), flags=re.I)
    if not m:
        return ""
    return m[1].upper() + (f"-{m[2]}" if m[2] else "")


def finish(row: dict) -> dict:
    """取得元ごとの生データに正規化キーを付ける"""
    row["cpu_key"] = cpu_key(row.get("cpu", ""))
    row["gpu_key"] = gpu_key(row.get("gpu", ""), row.get("kind", ""))
    row["mem_gb"] = gb(row.get("mem", ""))
    row["storage_gb"] = gb(row.get("storage_cap", "")) or gb(row.get("storage1", ""))
    return row
