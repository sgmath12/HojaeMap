# -*- coding: utf-8 -*-
"""공원·하천을 받아 data/osm-places.json 으로 저장.

사람들이 실제로 뛰는 데는 이면도로가 아니라 중앙공원 산책로와 탄천변입니다.
길 종류(footway/cycleway)만으로는 구분이 안 돼서 — 아파트 단지 안 보도도
footway라 — 공원 경계와 하천 선을 따로 받아 대조합니다.
"""
import json, os, sys, time, urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from region import CENTER, RADIUS
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]

Q = f"""
[out:json][timeout:180];
(
  way["leisure"~"^(park|garden|nature_reserve)$"](around:{RADIUS},{CENTER[0]},{CENTER[1]});
  way["landuse"~"^(recreation_ground|forest|grass|village_green)$"](around:{RADIUS},{CENTER[0]},{CENTER[1]});
  way["waterway"~"^(river|stream)$"](around:{RADIUS},{CENTER[0]},{CENTER[1]});
  way["natural"="water"](around:{RADIUS},{CENTER[0]},{CENTER[1]});
);
out geom;
"""


def ask(query, tries=3):
    last = None
    for _ in range(tries):
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


def main():
    els = ask(Q)
    parks, water = [], []
    for e in els:
        gm = e.get("geometry")
        if not gm or len(gm) < 3:
            continue
        ring = [[round(p["lat"], 6), round(p["lon"], 6)] for p in gm]
        t = e.get("tags", {})
        rec = {"ring": ring, "name": t.get("name", ""),
               "kind": t.get("leisure") or t.get("landuse") or t.get("natural", "")}
        if t.get("waterway") in ("river", "stream"):
            water.append(rec)
        else:
            parks.append(rec)
    out = os.path.join(os.path.dirname(__file__), "..", "data", "osm-places.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"parks": parks, "water": water}, f)
    print(f"공원·녹지 {len(parks)}개 · 하천 {len(water)}줄 → {os.path.abspath(out)} "
          f"{os.path.getsize(out)//1024}KB")


if __name__ == "__main__":
    main()
