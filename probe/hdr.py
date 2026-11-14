import json, sys, os
sys.path.insert(0, os.getcwd())
from pcprice import games
g = games.collect(None)
json.dump(g, open("probe_out/games.json", "w"), ensure_ascii=False, indent=1)
