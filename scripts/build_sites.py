"""Cloudflare Pages 用に2つのサイトを組み立てる

site/internal : 社内用（全タブ・3社データ）… Cloudflare Access でログイン必須にする
site/public   : お客様用（ドスパラのみ）… 他社データを含めず公開
"""
from __future__ import annotations

import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site")
DS = "ドスパラ"


def load(name, default):
    try:
        with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

    # 社内用：そのまま
    internal = os.path.join(OUT, "internal")
    shutil.copytree(os.path.join(ROOT, "data"), os.path.join(internal, "data"), ignore=shutil.ignore_patterns("cache", "baseline.json"))
    os.makedirs(os.path.join(internal, "output"))
    shutil.copy(os.path.join(ROOT, "output", "pc_price_latest.xlsx"), os.path.join(internal, "output"))
    with open(os.path.join(internal, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # お客様用：ドスパラのデータだけ。お客様表示に固定
    public = os.path.join(OUT, "public")
    d = os.path.join(public, "data")
    dump(os.path.join(d, "products.json"), [r for r in load("products.json", []) if r.get("maker") == DS])
    dump(os.path.join(d, "status.json"), [s for s in load("status.json", []) if s.get("maker") == DS])
    dump(os.path.join(d, "ds_fps.json"), load("ds_fps.json", {}))
    for name, empty in [("same_spec.json", []), ("changes.json", []), ("spec_history.json", {"dates": [], "series": {}}),
                        ("ds_featured.json", {}), ("ai_analysis.json", {}), ("ai_reco.json", {})]:
        dump(os.path.join(d, name), empty)
    pub = html.replace("<head>", "<head>\n<script>window.PC_PUBLIC = true;</script>", 1)
    pub = pub.replace("<title>PC価格調査 3社比較</title>", "<title>ドスパラ PC検索</title>")
    with open(os.path.join(public, "index.html"), "w", encoding="utf-8") as f:
        f.write(pub)
    print("built", internal, public)


if __name__ == "__main__":
    main()
