# -*- coding: utf-8 -*-
"""대상 지역 반경의 보행 가능 도로망을 Overpass에서 받아 data/osm-raw.json 으로 저장.
자동 생성 파일이므로 data/osm-raw.json 은 직접 고치지 마세요."""
import json, os, sys, time, urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from region import CENTER, RADIUS

# 달릴 수 있는 길만. 고속도로·차도 전용은 뺀다.
HW = "footway|path|pedestrian|living_street|residential|service|unclassified|tertiary|secondary|cycleway|steps|track"

Q = f"""
[out:json][timeout:300];
(
  way["highway"~"^({HW})$"](around:{RADIUS},{CENTER[0]},{CENTER[1]});
);
out body geom;
"""

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]


def ask(query, tries=3):
    last = None
    for _ in range(tries):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    url, data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "running-art/0.1"})
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.load(r)["elements"]
            except Exception as e:
                last = e
                print(f"  {url.split('/')[2]} 실패: {e}")
                time.sleep(4)
    raise SystemExit(f"Overpass 응답 없음: {last}")


def main():
    out = os.path.join(os.path.dirname(__file__), "..", "data", "osm-raw.json")
    els = ask(Q)
    print(f"way {len(els)}개")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"center": CENTER, "radius": RADIUS, "elements": els}, f)
    print("→", os.path.abspath(out), os.path.getsize(out) // 1024, "KB")

if __name__ == "__main__":
    main()
