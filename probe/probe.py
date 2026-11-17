import os, re, sys, urllib.request, json, time
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
urls = json.load(open(sys.argv[1]))
os.makedirs("probe_out", exist_ok=True)
for name, u in urls.items():
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ja"})
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(); code = r.status; final = r.geturl()
    except Exception as e:
        b = str(e).encode(); code = getattr(e, "code", "ERR"); final = u
    open(f"probe_out/{name}.html", "wb").write(b)
    print(f"{name}\t{code}\t{len(b)}\t{final}")
    time.sleep(1)
