# -*- coding: utf-8 -*-
"""신호등·횡단보도 노드를 받아 data/osm-nodes.json 으로 저장.

달릴 때 흐름을 끊는 건 차도 자체보다 신호 대기입니다. 큰길을 몇 번
건너느냐가 코스 체감을 크게 좌우해서 따로 받습니다.
"""
import json, os, sys, time, urllib.request, urllib.parse

# Overpass 본서버는 504를 자주 뱉습니다. 미러를 돌아가며 재시도합니다.
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]


def ask(query, tries=3):
    last = None
    for t in range(tries):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    url, data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "running-art/0.1"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.load(r)["elements"]
            except Exception as e:
                last = e
                print(f"  {url.split('/')[2]} 실패: {e}")
                time.sleep(3)
    raise SystemExit(f"Overpass 응답 없음: {last}")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from region import CENTER, RADIUS

Q = f"""
[out:json][timeout:120];
(
  node["highway"="traffic_signals"](around:{RADIUS},{CENTER[0]},{CENTER[1]});
  node["highway"="crossing"](around:{RADIUS},{CENTER[0]},{CENTER[1]});
);
out body;
"""

def main():
    els = ask(Q)
    sig = {e["id"]: ("signal" if e["tags"].get("highway") == "traffic_signals"
                     else "crossing") for e in els if "tags" in e}
    out = os.path.join(os.path.dirname(__file__), "..", "data", "osm-nodes.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(sig, f)
    n_s = sum(1 for v in sig.values() if v == "signal")
    print(f"신호등 {n_s}개 · 횡단보도 {len(sig)-n_s}개 → {os.path.abspath(out)}")

if __name__ == "__main__":
    main()
