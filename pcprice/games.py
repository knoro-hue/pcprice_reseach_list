"""市場の人気ゲーム（Steam 日本の売上上位）と、各ゲームの公式動作環境・日本語レビューの「重さ」傾向を集める。

- 人気順：Steam 検索「売上上位」（日本・ゲームのみ）
- 公式動作環境：Steam ストア API（appdetails）の pc_requirements
- 口コミ：Steam コミュニティの日本語レビュー（評価順 上位50件）に出てくる「重い／快適」などの語の件数
  （レビュー本文は保存しない。件数だけ）
- Steam 外の定番（Valorant など）は、ドスパラのゲーム性能データだけで扱う
週1回の更新で十分なので、前回から6日未満なら取得しない。
"""
import datetime as dt
import html
import json
import re
import urllib.parse

from .common import Http

TOP_N = 30          # 売上上位から取る数
REVIEW_PAGES = 5    # 10件×5ページ
# ドスパラ「ゲーム性能」のゲーム名 → Steam appid（Steam 外は None）
DS_GAMES = {"Apex Legends": 1172470, "Overwatch 2": 2357570, "Escape from Tarkov": 3932890, "Monster Hunter Wilds": 2246340,
            "グランド・セフト・オートＶ": 271590, "Valorant": None, "Fortnite": None, "League of Legends": None, "Minecraft": None}
NON_STEAM = {"Valorant": ("VALORANT", "https://playvalorant.com/ja-jp/"), "Fortnite": ("フォートナイト", "https://www.fortnite.com/"),
             "League of Legends": ("リーグ・オブ・レジェンド", "https://www.leagueoflegends.com/ja-jp/"),
             "Minecraft": ("マインクラフト（Java版）", "https://www.minecraft.net/ja-jp")}
HEAVY = re.compile(r"重い|重すぎ|重たい|カクつ|カクカク|カクる|フレームレートが(低|安定しな)|fps(が)?(出な|低|安定しな)|最適化(が|も)?(悪|不足|されて(い)?な|ひど|酷|甘)|スタッタ|クラッシュ|落ちる|ハイスペック(が)?必要|ゲーミングPCでも", re.I)
LIGHT = re.compile(r"軽い|軽量|快適に?(動|遊|プレイ)|ヌルヌル|サクサク|最適化(が|は)?(良|いい|素晴|しっかり)|低スペ(ック)?でも|ノートでも", re.I)


def _text(h: str) -> str:
    """要件HTMLを行ごとのテキストへ（見出し「最低:」「推奨:」を1行に）"""
    t = re.sub(r"<br\s*/?>|</li>|</p>|<li>", "\n", h or "")
    t = re.sub(r"<strong>(.*?)</strong>", r"\1", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    lines = [re.sub(r"\s+", " ", x).strip() for x in t.split("\n")]
    return "\n".join(x for x in lines if x)


def popular(http: Http, n=TOP_N):
    u = "https://store.steampowered.com/search/results/?filter=topsellers&cc=jp&l=japanese&json=1&count=60&category1=998"
    out = []
    for it in json.loads(http.get(u)).get("items", []):
        m = re.search(r"/apps/(\d+)", it.get("logo", ""))
        if m and int(m.group(1)) not in [x[0] for x in out]:
            out.append((int(m.group(1)), it.get("name", "")))
    return out[:n]


def details(http: Http, appid: int):
    d = json.loads(http.get(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=japanese&cc=jp"))
    x = (d.get(str(appid)) or {}).get("data") or {}
    req = x.get("pc_requirements") or {}
    if isinstance(req, list):
        req = {}
    return {"name": x.get("name"), "img": x.get("capsule_image") or x.get("header_image"), "free": bool(x.get("is_free")),
            "genres": [g.get("description") for g in x.get("genres", [])][:3],
            "release": (x.get("release_date") or {}).get("date"),
            "req": "\n".join(_text(req.get(k, "")) for k in ("minimum", "recommended") if req.get(k)),
            "type": x.get("type")}


def reviews(http: Http, appid: int):
    """評価順の日本語レビューを最大 REVIEW_PAGES×10 件読み、「重い／快適」系の語を含む件数を数える"""
    heavy = light = 0
    seen = set()
    u = f"https://steamcommunity.com/app/{appid}/reviews/?browsefilter=toprated&filterLanguage=japanese&p=1"
    for p in range(1, REVIEW_PAGES + 1):
        try:
            h = http.get(u)
        except Exception:  # noqa: BLE001
            break
        cards = re.findall(r'<div class="apphub_CardTextContent">(.*?)</div>\s*</div>', h, re.S)
        new = 0
        for c in cards:
            t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            if t in seen:
                continue
            seen.add(t)
            new += 1
            heavy += bool(HEAVY.search(t))
            light += bool(LIGHT.search(t))
        cur = re.search(r'name="userreviewscursor" value="([^"]*)"', h)
        if not new or not cur:
            break
        u = (f"https://steamcommunity.com/app/{appid}/homecontent/?userreviewscursor={urllib.parse.quote(cur.group(1))}"
             f"&userreviewsoffset={p * 10}&p={p + 1}&numperpage=10&browsefilter=toprated&appid={appid}&appHubSubSection=10"
             f"&l=japanese&filterLanguage=japanese&searchText=&maxInappropriateScore=50&forceanon=1")
    return {"n": len(seen), "heavy": heavy, "light": light}


def collect(prev=None, today=None, log=print, force=False):
    today = today or dt.date.today().isoformat()
    if prev and not force and prev.get("updated"):
        try:
            if (dt.date.fromisoformat(today) - dt.date.fromisoformat(prev["updated"])).days < 6:
                log(f"人気ゲーム：前回 {prev['updated']} から6日未満のため取得しない")
                return None
        except ValueError:
            pass
    http = Http(wait=1.5)
    pop = popular(http)
    ids = [a for a, _ in pop] + [a for a in DS_GAMES.values() if a and a not in [x for x, _ in pop]]
    ds_by_app = {a: g for g, a in DS_GAMES.items() if a}
    games = []
    for i, appid in enumerate(ids):
        try:
            d = details(http, appid)
        except Exception as e:  # noqa: BLE001
            log(f"人気ゲーム appdetails 失敗 {appid}: {e}")
            continue
        if d.get("type") not in (None, "game") or not d.get("req"):
            continue
        g = {"rank": i + 1 if i < len(pop) else None, "appid": appid, "name": d["name"] or dict(pop).get(appid, ""),
             "url": f"https://store.steampowered.com/app/{appid}/", "img": d["img"], "free": d["free"], "genres": d["genres"],
             "release": d["release"], "req": d["req"], "ds": ds_by_app.get(appid), "steam": True}
        g["reviews"] = reviews(http, appid)
        games.append(g)
    for g, (name, url) in NON_STEAM.items():
        games.append({"rank": None, "appid": None, "name": name, "url": url, "img": None, "free": True, "genres": [],
                      "release": None, "req": "", "ds": g, "steam": False, "reviews": None})
    log(f"人気ゲーム：{len(games)}本（Steam売上上位 {len(pop)}本から／通信 {http.count}回）")
    return {"updated": today, "source": "Steam 売上上位（日本）", "games": games}
