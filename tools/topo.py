"""TopoJSON 디코더 + 행정동 중심점 계산.

geo-*.json은 TopoJSON이라 좌표가 델타 인코딩되어 있습니다. 여기서 실좌표로
풀어내고, 각 행정동의 대표점(폴리곤 내부가 보장되는 점)과 면적을 구합니다.
"""

import json
import math

from shapely.geometry import Polygon, MultiPolygon


def decode_arcs(topo):
    """델타 인코딩된 arc를 실좌표 리스트로 변환."""
    tr = topo.get("transform")
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            if tr:
                pts.append((x * tr["scale"][0] + tr["translate"][0],
                            y * tr["scale"][1] + tr["translate"][1]))
            else:
                pts.append((x, y))
        out.append(pts)
    return out


def ring(arcs, idxs):
    """arc 인덱스 목록을 하나의 링으로 잇는다. 음수 인덱스는 역방향(~i)."""
    pts = []
    for i in idxs:
        a = arcs[~i][::-1] if i < 0 else arcs[i]
        pts.extend(a[1:] if pts else a)
    return pts


def shape_of(geom, arcs):
    t = geom["type"]
    if t == "Polygon":
        rings = [ring(arcs, r) for r in geom["arcs"]]
        rings = [r for r in rings if len(r) >= 4]
        return Polygon(rings[0], rings[1:]) if rings else None
    if t == "MultiPolygon":
        polys = []
        for poly in geom["arcs"]:
            rings = [ring(arcs, r) for r in poly]
            rings = [r for r in rings if len(r) >= 4]
            if rings:
                polys.append(Polygon(rings[0], rings[1:]))
        return MultiPolygon(polys) if polys else None
    return None


def interior_grid(shape, n=200):
    """폴리곤 내부를 균등 격자로 샘플링한 점 목록."""
    from shapely.prepared import prep
    from shapely.geometry import Point

    minx, miny, maxx, maxy = shape.bounds
    k = int(math.sqrt(n)) + 1
    pre = prep(shape)
    pts = []
    for i in range(k):
        x = minx + (maxx - minx) * (i + 0.5) / k
        for j in range(k):
            y = miny + (maxy - miny) * (j + 0.5) / k
            if pre.contains(Point(x, y)):
                pts.append((x, y))
    return pts


def load_shapes(path):
    """[(code, name, shapely도형), ...] 반환."""
    topo = json.load(open(path, encoding="utf-8"))
    arcs = decode_arcs(topo)
    key = list(topo["objects"].keys())[0]

    out = []
    for g in topo["objects"][key]["geometries"]:
        s = shape_of(g, arcs)
        if s is None or s.is_empty:
            continue
        if not s.is_valid:
            s = s.buffer(0)
        if s.is_empty:
            continue
        out.append((str(g["properties"]["code"]), g["properties"]["name"], s))
    return out


def load_regions(path):
    """[{code, name, lat, lng, area_km2}, ...] 반환.

    lat/lng는 폴리곤의 대표점입니다. 큰 동에서는 사람이 사는 곳과 크게
    어긋날 수 있으니, 거리 계산에는 build-metrics.py 의 활동 중심점을 쓰세요.
    """
    out = []
    for code, name, s in load_shapes(path):
        p = s.representative_point()
        km2 = s.area * (111.32 ** 2) * math.cos(math.radians(p.y))
        out.append({
            "code": code, "name": name,
            "lat": round(p.y, 6), "lng": round(p.x, 6),
            "area_km2": round(km2, 3),
        })
    return out


if __name__ == "__main__":
    import sys
    rs = load_regions(sys.argv[1] if len(sys.argv) > 1 else "geo-dong.json")
    print(f"{len(rs)}개 지역")
    for r in rs[:5]:
        print(" ", r)
