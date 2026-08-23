# -*- coding: utf-8 -*-
"""osm-raw.json → 보행 도로 그래프. 좌표는 중심 기준 로컬 미터로 투영합니다."""
import json, os, math
import numpy as np
from collections import defaultdict

# 러닝 쾌적도 가중치. 1.0이 가장 좋고, 클수록 피하고 싶은 길.
COMFORT = {
    "footway": 1.0, "path": 1.0, "pedestrian": 1.0, "cycleway": 1.05,
    "living_street": 1.1, "residential": 1.2, "service": 1.3,
    "unclassified": 1.3, "track": 1.4, "steps": 3.0,
    "tertiary": 1.6, "secondary": 2.2,
}

# 러닝에 불리한 길. 뛰다 멈추게 만드는 것들입니다.
MAJOR = {"tertiary", "secondary", "primary"}
SCENIC_PREF = 1.0      # 라우팅은 손대지 않습니다. 아래 주석 참고
ELEV_CELL = 0.0003


def _area(r):
    """위경도 링의 면적(㎡)."""
    la = math.radians(r[:, 0].mean())
    x = r[:, 1] * 111320 * math.cos(la)
    y = r[:, 0] * 110540
    return abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2


def load_side(name):
    """고도·신호등 같은 곁들이 데이터. 없으면 조용히 빈 값으로 둡니다."""
    p = os.path.join(os.path.dirname(__file__), "..", "data", name)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load(path=None):
    path = path or os.path.join(os.path.dirname(__file__), "..", "data", "osm-raw.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)

class Graph:
    def __init__(self, raw):
        self.center = tuple(raw["center"])
        self.radius = raw["radius"]
        lat0 = math.radians(self.center[0])
        self.mx = 111320.0 * math.cos(lat0)   # 경도 1도의 미터
        self.my = 110540.0                     # 위도 1도의 미터
        self.elev_grid = load_side("elev.json")
        self.signal_tags = load_side("osm-nodes.json")
        self.places = load_side("osm-places.json")
        self._build(raw["elements"])
        self._attach()

    def to_xy(self, lat, lon):
        return ((lon - self.center[1]) * self.mx, (lat - self.center[0]) * self.my)

    def to_ll(self, x, y):
        return (y / self.my + self.center[0], x / self.mx + self.center[1])

    def _build(self, els):
        pos = {}                  # nid -> (x, y)
        adj = defaultdict(dict)   # nid -> {nid: (길이, 비용, 도로종류)}
        for w in els:
            hw = w.get("tags", {}).get("highway", "")
            geom, nodes = w.get("geometry"), w.get("nodes")
            if not geom or not nodes or len(nodes) != len(geom):
                continue
            c = COMFORT.get(hw, 1.5)
            for i in range(len(nodes)):
                pos[nodes[i]] = self.to_xy(geom[i]["lat"], geom[i]["lon"])
            for i in range(len(nodes) - 1):
                a, b = nodes[i], nodes[i + 1]
                if a == b:
                    continue
                (ax, ay), (bx, by) = pos[a], pos[b]
                d = math.hypot(bx - ax, by - ay)
                if d == 0:
                    continue
                cost = d * c
                for u, v in ((a, b), (b, a)):
                    old = adj[u].get(v)
                    if old is None or cost < old[1]:
                        adj[u][v] = (d, cost, hw)
        # 가장 큰 연결 요소만 남긴다 (뚝 떨어진 길은 코스가 안 됨)
        seen, best = set(), []
        for start in adj:
            if start in seen:
                continue
            comp, stack = [], [start]
            seen.add(start)
            while stack:
                u = stack.pop()
                comp.append(u)
                for v in adj[u]:
                    if v not in seen:
                        seen.add(v); stack.append(v)
            if len(comp) > len(best):
                best = comp
        keep = set(best)
        self.ids = np.array(sorted(keep), dtype=np.int64)
        self.idx = {int(n): i for i, n in enumerate(self.ids)}
        self.xy = np.array([pos[int(n)] for n in self.ids], dtype=np.float64)
        head, nbr, w_len, w_cost, w_type = [0], [], [], [], []
        self.types = []
        tidx = {}
        for n in self.ids:
            for v, (d, c, hw) in adj[int(n)].items():
                if v in keep:
                    if hw not in tidx:
                        tidx[hw] = len(self.types); self.types.append(hw)
                    nbr.append(self.idx[v]); w_len.append(d)
                    w_cost.append(c); w_type.append(tidx[hw])
            head.append(len(nbr))
        self.wtype = np.array(w_type, dtype=np.int16)
        self.is_steps = np.array([t == "steps" for t in self.types])
        self.is_major = np.array([t in MAJOR for t in self.types])
        self.head = np.array(head, dtype=np.int32)
        self.nbr = np.array(nbr, dtype=np.int32)
        self.wlen = np.array(w_len, dtype=np.float64)
        self.wcost = np.array(w_cost, dtype=np.float64)

    def _attach(self):
        """노드별 고도와 신호등 여부를 붙인다."""
        z = np.zeros(len(self.ids))
        have = 0
        for i, (x, y) in enumerate(self.xy):
            la, lo = self.to_ll(x, y)
            v = self.elev_grid.get(f"{round(la/ELEV_CELL)},{round(lo/ELEV_CELL)}")
            if v is not None:
                z[i] = v; have += 1
        self.z = z
        self.has_elev = have / max(1, len(self.ids))
        # 신호등은 1.0, 신호 없는 횡단보도는 0.35만 셉니다 (기다림이 짧아서)
        w = np.zeros(len(self.ids))
        for i, nid in enumerate(self.ids):
            t = self.signal_tags.get(str(int(nid)))
            if t == "signal":
                w[i] = 1.0
            elif t == "crossing":
                w[i] = 0.35
        self.signal = w
        self.scenic = self._scenic()
        # 간선의 양 끝이 다 공원·하천이면 표시해 둡니다.
        # 라우팅 비용을 깎아봤더니 경로가 외곽선에서 끌려나가 모양이 망가졌습니다
        # (나뭇잎 닮음 55.7 → 46.3). 그래서 선호는 '어디에 놓을지'(배치 후보
        # 순위)에서만 겁니다. SCENIC_PREF는 1.0으로 두세요.
        src = np.zeros(len(self.nbr), dtype=np.int32)
        for u in range(len(self.ids)):
            src[self.head[u]:self.head[u + 1]] = u
        self.esrc = src
        self.escenic = self.scenic[src] & self.scenic[self.nbr]
        self.wcost = np.where(self.escenic, self.wcost * SCENIC_PREF, self.wcost)

    def _scenic(self):
        """공원 안이거나 하천변인 노드 = 사람들이 실제로 뛰는 길.

        길 종류만으로는 안 됩니다. 아파트 단지 안 보도도 footway라
        중앙공원 산책로와 구분이 안 됩니다.

        녹지 219개 중 177개가 1ha 미만 단지 조경입니다. 그것까지 세면
        주택가 노드의 23%가 '공원'으로 잡혀 구분이 무의미해집니다.
        1ha 이상만, grass는 제외하고 셉니다."""
        if not self.places:
            return np.zeros(len(self.ids), dtype=bool)
        from matplotlib.path import Path
        ll = np.array([self.to_ll(x, y) for x, y in self.xy])
        flag = np.zeros(len(self.ids), dtype=bool)
        self.park_names = []
        for rec in self.places.get("parks", []):
            ring = rec["ring"] if isinstance(rec, dict) else rec
            kind = rec.get("kind", "") if isinstance(rec, dict) else ""
            if kind in ("grass", "village_green"):
                continue
            r = np.array(ring)
            if len(r) < 3 or _area(r) < 10000:
                continue
            self.park_names.append(rec.get("name", "") if isinstance(rec, dict) else "")
            box = ((ll[:, 0] >= r[:, 0].min()) & (ll[:, 0] <= r[:, 0].max()) &
                   (ll[:, 1] >= r[:, 1].min()) & (ll[:, 1] <= r[:, 1].max()))
            if not box.any():
                continue
            inside = Path(r).contains_points(ll[box])
            idx = np.nonzero(box)[0][inside]
            flag[idx] = True
        # 하천은 선이라 둔치 폭만큼(120m) 잡아줍니다
        pts = [p for line in self.places.get("water", [])
               for p in (line["ring"] if isinstance(line, dict) else line)]
        if pts:
            from scipy.spatial import cKDTree
            w = np.array([self.to_xy(a, b) for a, b in pts])
            d = cKDTree(w).query(self.xy)[0]
            flag |= d < 120
        return flag

    def stats(self):
        return dict(nodes=len(self.ids), edges=len(self.nbr) // 2,
                    km=round(float(self.wlen.sum()) / 2000, 1),
                    고도=f"{self.has_elev:.0%}",
                    신호=int((self.signal > 0).sum()),
                    공원하천=f"{self.scenic.mean():.0%}",
                    공원하천길=f"{float(self.wlen[self.escenic].sum())/2000:.0f}km")

if __name__ == "__main__":
    g = Graph(load())
    print(g.stats())
