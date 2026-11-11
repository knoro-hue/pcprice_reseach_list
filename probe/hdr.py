import urllib.request
r=urllib.request.Request("https://r.jina.ai/https://jp.finalfantasyxiv.com/system_requirements/",headers={"Origin":"https://knoro-hue.github.io","User-Agent":"Mozilla/5.0"})
with urllib.request.urlopen(r,timeout=60) as res:
    print(res.status); [print(k,v) for k,v in res.headers.items() if 'access' in k.lower() or 'type' in k.lower()]
