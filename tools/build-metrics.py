#!/usr/bin/env python3
"""행정동 3,504개의 위치 기반 지표를 계산해 data-metrics.js를 만든다.

사람 판단이 아니라 좌표 계산으로 나오는 값만 다룬다.

  transport      가장 가까운 지하철·전철역까지 거리 + 반경 내 역 개수
  school_access  반경 내 초·중·고 밀도
  life_density   반경 내 공원·카페 밀도

원본은 OpenStreetMap(Overpass API). tools/cache/ 에 캐시하므로 두 번째
실행부터는 네트워크를 쓰지 않는다.

  python3 tools/build-metrics.py            # 캐시 있으면 재사용
  python3 tools/build-metrics.py --refresh  # 원본 다시 받기

⚠️ 이름을 조심할 것. 검증해보니 셋의 품질이 다르다.

   transport 는 실제와 잘 맞는다 (정자동 290m/95점 vs 분당동 2447m/56점).
   그래서 행정동의 교통 점수는 이 계산값으로 대체한다.

   school_access 는 "학군"이 아니다. 상위권이 중림동·청파동·돈암1동처럼 그냥
   오래된 고밀 주거지이고 대치1동은 172위에 그친다. 학원가가 아니라 학교 밀도를,
   사실상 인구 밀도를 재고 있다. 학군 점수로 쓰면 안 된다.

   life_density 도 "라이프"가 아니다. 상위권이 소공동·명동·회현동처럼 도심
   상업지구다. 거주 쾌적성이 아니라 상업 POI 밀도를 잰다.

   그래서 뒤의 둘은 참고 지표로만 노출하고 손으로 매긴 학군·라이프 점수를
   덮어쓰지 않는다.

중심점을 어떻게 잡는가:
   행정동은 산·논밭까지 포함해서 넓다. 폴리곤의 기하학적 중심을 그대로 쓰면
   사람이 사는 곳과 크게 어긋난다. 구미1동이 그랬다 — 대표점 기준 오리역까지
   2,296m가 나오는데 실제 주거지는 역 바로 옆이다.
   그래서 내부를 격자로 훑고 각 점을 반경 400m 안의 카페·학교 수로 가중해
   '활동 중심점'을 구한다. 같은 방식으로 구미1동은 401m가 된다.
   POI가 하나도 없는 동(산간·농촌)은 대표점으로 되돌린다.
"""

import json
import math
import os
import sys
import urllib.request

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from topo import load_shapes, interior_grid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", "cache")
OVERPASS = "https://overpass-api.de/api/interpreter"

# 위경도 → 미터 근사. 전국 단일 기준위도라 거리 오차는 ±2% 수준.
LAT0 = 36.5
M_PER_DEG_LAT = 110574.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(LAT0))

QUERIES = {
    "stations": """
[out:json][timeout:300];
area["ISO3166-1"="KR"][admin_level=2]->.kr;
(
  node["railway"="station"]["station"="subway"](area.kr);
  node["railway"="station"]["subway"="yes"](area.kr);
  node["station"="light_rail"](area.kr);
  node["railway"="station"]["train"="yes"](area.kr);
);
out body;
""",
    "schools": """
[out:json][timeout:300];
area["ISO3166-1"="KR"][admin_level=2]->.kr;
(
  nwr["amenity"="school"](area.kr);
);
out center tags;
""",
    "life": """
[out:json][timeout:300];
area["ISO3166-1"="KR"][admin_level=2]->.kr;
(
  nwr["leisure"="park"](area.kr);
  node["amenity"="cafe"](area.kr);
);
out center tags;
""",
}


def fetch(name, refresh=False):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name + ".json")
    if os.path.exists(path) and not refresh:
        return json.load(open(path, encoding="utf-8"))
    print(f"  Overpass에서 {name} 받는 중...", flush=True)
    req = urllib.request.Request(OVERPASS, data=QUERIES[name].encode("utf-8"))
    with urllib.request.urlopen(req, timeout=400) as r:
        data = json.loads(r.read().decode("utf-8"))
    json.dump(data, open(path, "w", encoding="utf-8"))
    return data


def coords(elements, keep=None):
    """OSM 요소에서 (lat, lng, tags) 뽑기. way/relation은 center 사용."""
    out = []
    for e in elements:
        lat = e.get("lat", (e.get("center") or {}).get("lat"))
        lon = e.get("lon", (e.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        t = e.get("tags", {})
        if keep and not keep(t):
            continue
        out.append((lat, lon, t))
    return out


def to_xy(pts):
    if not pts:
        return np.zeros((0, 2))
    a = np.array([(p[1] * M_PER_DEG_LON, p[0] * M_PER_DEG_LAT) for p in pts])
    return a


def dedupe(pts, tol=120.0):
    """같은 역이 여러 노드로 잡히는 경우를 좌표 기준으로 합친다."""
    if not pts:
        return pts
    xy = to_xy(pts)
    tree = cKDTree(xy)
    keep, seen = [], set()
    for i in range(len(pts)):
        if i in seen:
            continue
        keep.append(i)
        for j in tree.query_ball_point(xy[i], tol):
            seen.add(j)
    return [pts[i] for i in keep]


def piecewise(d, table):
    """거리 d(m)를 (거리, 점수) 구간표에 선형 보간."""
    xs = [t[0] for t in table]
    ys = [t[1] for t in table]
    return float(np.interp(d, xs, ys))


def pct_rank(vals):
    """순위 백분위 (0~1). 동점은 평균 순위."""
    v = np.asarray(vals, dtype=float)
    order = v.argsort()
    ranks = np.empty(len(v))
    ranks[order] = np.arange(len(v))
    # 동점 평균 처리
    uniq, inv, cnt = np.unique(v, return_inverse=True, return_counts=True)
    sums = np.zeros(len(uniq))
    np.add.at(sums, inv, ranks)
    ranks = (sums / cnt)[inv]
    return ranks / max(len(v) - 1, 1)


def main():
    refresh = "--refresh" in sys.argv

    print("행정동 경계 읽는 중...")
    shapes = load_shapes(os.path.join(ROOT, "geo-dong.json"))
    print(f"  {len(shapes)}개")

    # 활동 중심점 — 사람이 실제로 모여 있는 쪽으로 당긴 대표 좌표
    print("활동 중심점 계산 중... (격자 샘플링, 1~2분)")
    poi = coords(fetch("life", refresh)["elements"]) + \
          coords(fetch("schools", refresh)["elements"])
    ptree = cKDTree(to_xy(poi))

    regions, fallback = [], 0
    for code, name, shp in shapes:
        grid = interior_grid(shp)
        rp = shp.representative_point()
        if grid:
            gxy = np.array([(x * M_PER_DEG_LON, y * M_PER_DEG_LAT) for x, y in grid])
            w = np.array([len(h) for h in ptree.query_ball_point(gxy, 400)], dtype=float)
            if w.sum() > 0:
                lng, lat = (np.array(grid) * w[:, None]).sum(axis=0) / w.sum()
            else:
                lng, lat, fallback = rp.x, rp.y, fallback + 1
        else:
            lng, lat, fallback = rp.x, rp.y, fallback + 1
        regions.append({"code": code, "name": name,
                        "lat": round(float(lat), 6), "lng": round(float(lng), 6)})
    print(f"  POI 없어 대표점으로 되돌린 동 {fallback}개")

    rxy = to_xy([(r["lat"], r["lng"]) for r in regions])

    # ── 역세권 ────────────────────────────────────────────────
    print("역세권 계산 중...")
    st = coords(fetch("stations", refresh)["elements"])
    st = dedupe(st)
    print(f"  역 {len(st)}개 (중복 병합 후)")
    stree = cKDTree(to_xy(st))
    dist, _ = stree.query(rxy)
    n1k = np.array([len(x) for x in stree.query_ball_point(rxy, 1000)])

    DIST_TABLE = [(0, 97), (300, 95), (500, 90), (800, 82),
                  (1200, 73), (2000, 61), (3000, 49), (5000, 34),
                  (10000, 18), (30000, 8)]
    transport = np.array([piecewise(d, DIST_TABLE) for d in dist])
    transport += np.minimum(8, np.maximum(0, n1k - 1) * 4)   # 복수 역 가산
    transport = np.clip(np.round(transport), 5, 100)

    # ── 학교 밀도 ─────────────────────────────────────────────
    print("학교 밀도 계산 중...")
    sc = coords(fetch("schools", refresh)["elements"])
    lvl = lambda t, c: c in (t.get("isced:level") or "")
    elem = [p for p in sc if lvl(p[2], "1")]
    mid  = [p for p in sc if lvl(p[2], "2")]
    high = [p for p in sc if lvl(p[2], "3")]
    print(f"  초 {len(elem)} · 중 {len(mid)} · 고 {len(high)}")

    def within(pts, radius):
        if not pts:
            return np.zeros(len(regions))
        t = cKDTree(to_xy(pts))
        return np.array([len(x) for x in t.query_ball_point(rxy, radius)])

    raw_school = within(elem, 1500) + 2 * within(mid, 2000) + 2 * within(high, 2500)
    school_access = np.round(30 + pct_rank(raw_school) * 66)

    # ── 라이프 ────────────────────────────────────────────────
    print("생활 인프라 계산 중...")
    lf = fetch("life", refresh)["elements"]
    parks = coords(lf, keep=lambda t: t.get("leisure") == "park")
    cafes = coords(lf, keep=lambda t: t.get("amenity") == "cafe")
    print(f"  공원 {len(parks)} · 카페 {len(cafes)}")

    raw_life = within(parks, 1000) * 1.5 + within(cafes, 500)
    life_density = np.round(30 + pct_rank(raw_life) * 66)

    # ── 출력 ──────────────────────────────────────────────────
    rows = {}
    for i, r in enumerate(regions):
        rows[r["code"]] = {
            "transport": int(transport[i]),
            "school_access": int(school_access[i]),
            "life_density": int(life_density[i]),
            "station_m": int(round(dist[i])),
            "station_n": int(n1k[i]),
            "lat": r["lat"], "lng": r["lng"],
        }

    out = os.path.join(ROOT, "data-metrics.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("// 자동 생성 — 직접 고치지 마세요.\n")
        f.write("// 생성: python3 tools/build-metrics.py\n")
        f.write("// 출처: OpenStreetMap (ODbL) — 역·학교·공원·카페 좌표\n")
        f.write("//\n")
        f.write("// transport      최단 역거리 + 반경 1km 역 개수 — 교통 점수를 대체함\n")
        f.write("// school_access  반경 내 초·중·고 밀도의 전국 백분위 (학군 아님, 참고용)\n")
        f.write("// life_density   반경 내 공원·카페 밀도의 전국 백분위 (라이프 아님, 참고용)\n")
        f.write("// station_m      최단 역까지 거리(m) · station_n  1km 내 역 개수\n")
        f.write("// lat/lng        활동 중심점 (POI 가중, 기하학적 중심이 아님)\n")
        f.write("const METRICS = ")
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print(f"\n{out} 생성 — {len(rows)}개 동")
    print(f"  역까지 중앙값 {int(np.median(dist))}m · 1km 내 역 없는 동 {(n1k == 0).sum()}개")


if __name__ == "__main__":
    main()
