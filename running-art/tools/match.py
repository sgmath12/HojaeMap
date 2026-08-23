# -*- coding: utf-8 -*-
"""도로망에서 모양이 나올 자리를 찾는다.

방식: 모양을 지도 위 여러 위치·크기·각도로 놓아보고 → 각 꼭짓점을 가까운
도로 교차점에 붙이고 → 그 사이를 실제 도로로 이어 → 원래 모양과 얼마나
닮았는지 잰다. 닮은 정도만 보면 안 되고 "달릴 만한가"(되돌아오기 비율,
차도 비중, 총 거리)도 같이 봐야 코스로 쓸 수 있다.
"""
import heapq, json, math, os, sys, time
import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph import Graph, load
from shapes import SHAPES, resample

N_ANCHOR = 34          # 모양을 몇 점으로 나눠 도로에 붙일지
SCALES = [260, 350, 480, 650, 850, 1100, 1400]   # 모양 반폭(m)
# 260m는 중앙공원(46ha) 안에 들어가는 크기입니다.
ROTS = list(range(0, 360, 30))
GRID = 220             # 후보 중심 격자 간격(m)
ROUTE_TOP = 2400        # 실제로 경로까지 계산해볼 배치 수
OFF_K = 10.0           # 외곽선 이탈 벌점 세기
OFF_CLIP = 0.5         # 이탈 벌점 상한 (반폭의 50%를 넘으면 더 안 늘림)
SCENIC_MIN = 0.45      # 이만큼 공원·하천을 끼면 "산책로 코스"로 봅니다
KEEP_SCENIC = 5        # 모양당 산책로 코스를 몇 개까지 남길지
KEEP = 14              # 모양당 남길 후보 수 (자리를 흩어놓으려면 넉넉히)
from region import SEARCH_R, LEN_MIN, LEN_MAX, LEN_BEST


class Router:
    def __init__(self, g):
        self.g = g
        self.tree = cKDTree(g.xy)

    def path(self, s, t, off=None, k=0.0):
        """s→t 최단 경로. off가 주어지면 외곽선에서 멀어질수록 비싸진다.

        단순 최단경로만 쓰면 두 꼭짓점 사이를 가로질러 버려서 모양이 뭉갠다.
        외곽선까지의 거리(off)를 비용에 섞어야 선이 모양을 따라간다."""
        g = self.g
        if s == t:
            return [s], 0.0
        straight = math.dist(g.xy[s], g.xy[t])
        # 컷오프는 벌점이 곱해진 비용 기준이어야 한다. 거리로만 자르면
        # 벌점이 붙은 정상 경로까지 전부 잘려 나간다.
        cutoff = (straight * 4.0 + 250.0) * (1.0 + k * OFF_CLIP) * 1.5
        dist = {s: 0.0}
        prev = {}
        pq = [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e18):
                continue
            if u == t:
                break
            if d > cutoff:
                return None, None
            for e in range(g.head[u], g.head[u + 1]):
                v = int(g.nbr[e])
                w = g.wcost[e]
                if off is not None:
                    w *= 1.0 + k * min(OFF_CLIP, (off[u] + off[v]) * 0.5)
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if t not in dist:
            return None, None
        out, u = [t], t
        while u != s:
            u = prev[u]
            out.append(u)
        out.reverse()
        return out, dist[t]


def edge_len(g, u, v):
    for e in range(g.head[u], g.head[u + 1]):
        if g.nbr[e] == v:
            return g.wlen[e], g.wcost[e] / g.wlen[e], int(g.wtype[e])
    return 0.0, 1.5, -1


def climb_of(xy, z):
    """누적 오르막(m). 반드시 평활한 뒤에 더해야 합니다.

    SRTM 30m는 수직 오차가 ±5~10m라, 25m 간격 점마다 차이를 그대로 더하면
    없는 오르막이 쌓입니다. 실제로 고저차 53m인 코스가 285m로 나왔습니다
    (5.4배). 100m로 재샘플하고 500m 창으로 평활하면 59m — 언덕을 한 번
    오르내린 값과 맞습니다."""
    d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    if d[-1] < 300:
        return 0.0
    t = np.arange(0, d[-1], 100.0)
    zz = np.interp(t, d, z)
    if len(zz) < 6:
        return float(np.maximum(0, np.diff(zz)).sum())
    k = np.ones(5) / 5
    sm = np.convolve(zz, k, mode="same")
    sm[:5], sm[-5:] = zz[:5], zz[-5:]
    return float(np.maximum(0.0, np.diff(sm)).sum())


def run_score(m):
    """달릴 만한가를 0~100으로. 낮으면 그림은 되어도 뛰기 괴로운 코스입니다.

    깎는 항목은 전부 '뛰다 멈추게 하는 것'입니다. 계단·신호는 흐름을 끊고,
    오르막은 페이스를 무너뜨리고, 큰길은 매연과 소음이 붙습니다."""
    v = 100.0
    v -= 1.6 * max(0.0, m["climb_km"] - 10.0)   # km당 10m까진 평지로 봅니다
    v -= 6.0 * m["signal_km"]                   # 신호 하나에 6점
    v -= 0.05 * m["steps_m"]                    # 계단은 달리기가 아예 끊깁니다
    v -= 45.0 * m["major_r"]                    # 큰길 비중
    v -= 40.0 * m["repeat"]                     # 왔던 길 되돌기
    v += 20.0 * m["scenic_r"]                   # 공원·하천길은 되레 얹어줍니다
    lo, hi = LEN_BEST                           # 뛸 만한 거리에서 벗어난 만큼 깎기
    if m["len_m"] < lo:
        v -= (lo - m["len_m"]) / 1000.0 * 8.0
    elif m["len_m"] > hi:
        v -= (m["len_m"] - hi) / 1000.0 * 6.0
    return max(0.0, min(100.0, v))


def densify(pts, step=15.0):
    out = []
    for a, b in zip(pts[:-1], pts[1:]):
        d = math.dist(a, b)
        n = max(1, int(d // step))
        for i in range(n):
            f = i / n
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    out.append(tuple(pts[-1]))
    return np.array(out)


def evaluate(g, router, ideal, nodes_seq):
    """이어붙인 코스를 원래 모양과 비교해 점수를 낸다."""
    path = []
    for i in range(len(nodes_seq)):
        seg = nodes_seq[i]
        path.extend(seg if not path else seg[1:])
    if len(path) < 8:
        return None
    xy = g.xy[path]
    # 총 거리·되돌아오기·쾌적도
    total = weighted = steps_m = major_m = scenic_m = 0.0
    used = {}
    for u, v in zip(path[:-1], path[1:]):
        L, c, t = edge_len(g, u, v)
        total += L
        weighted += L * c
        if t >= 0 and g.is_steps[t]:
            steps_m += L
        if t >= 0 and g.is_major[t]:
            major_m += L
        if g.scenic[u] and g.scenic[v]:
            scenic_m += L
        k = (min(u, v), max(u, v))
        used[k] = used.get(k, 0) + 1
    if not (LEN_MIN <= total <= LEN_MAX):
        return None
    climb = climb_of(xy, g.z[path])
    repeat = sum(edge_len(g, *k)[0] * (n - 1) for k, n in used.items() if n > 1)
    repeat_r = repeat / total
    comfort = weighted / total
    km = total / 1000.0
    signals = float(g.signal[list(set(path))].sum())
    run = run_score(dict(climb_km=climb / km, signal_km=signals / km,
                         steps_m=steps_m, major_r=major_m / total,
                         repeat=repeat_r, scenic_r=scenic_m / total,
                         len_m=total))

    dense = densify(xy)
    it = cKDTree(ideal)
    pt = cKDTree(dense)
    scale = max(np.ptp(ideal[:, 0]), np.ptp(ideal[:, 1])) / 2.0
    d1 = it.query(dense)[0]              # 코스가 모양에서 얼마나 벗어났나
    d2 = pt.query(ideal)[0]              # 모양 중 코스가 안 지나간 데
    m1, m2 = d1.mean() / scale, d2.mean() / scale
    p90 = np.percentile(d1, 90) / scale

    fid = 100.0 * math.exp(-(0.45 * m1 + 0.35 * m2 + 0.20 * p90) / 0.055)
    # 닮음과 러닝을 곱해서 섞습니다. 어느 한쪽이 0이면 코스로 못 씁니다 —
    # 그림만 예쁘고 계단·신호 범벅인 코스를 위로 올리지 않으려는 겁니다.
    score = (fid ** 0.65) * (run ** 0.35)
    return dict(score=round(score, 1), fidelity=round(fid, 1), run=round(run, 1),
                km=round(km, 2), repeat=round(repeat_r, 3),
                comfort=round(comfort, 2), climb=round(climb),
                climb_km=round(climb / km, 1), signals=round(signals, 1),
                signal_km=round(signals / km, 1), steps_m=round(steps_m),
                major_r=round(major_m / total, 3),
                scenic_r=round(scenic_m / total, 3),
                dev_mean=round(m1, 3), gap_mean=round(m2, 3), path=path)


def place(shape, cx, cy, s, rot, flip):
    a = math.radians(rot)
    p = shape.copy()
    p[:, 0] *= flip
    ca, sa = math.cos(a), math.sin(a)
    x = p[:, 0] * ca - p[:, 1] * sa
    y = p[:, 0] * sa + p[:, 1] * ca
    return np.stack([x * s + cx, y * s + cy], 1)


def search(g, router, key, name, shape, verbose=True):
    del g          # router가 그래프를 들고 있습니다
    g = router.g
    anchors = resample(shape, N_ANCHOR)
    dense_ideal = resample(shape, 400)
    centers = []
    n = int(SEARCH_R / GRID)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            cx, cy = i * GRID, j * GRID
            if math.hypot(cx, cy) <= SEARCH_R:
                centers.append((cx, cy))
    centers = np.array(centers, dtype=np.float64)

    cands = []
    for s in SCALES:
        for rot in ROTS:
            for flip in (1, -1):
                base = place(anchors, 0, 0, s, rot, flip)      # (N,2)
                pts = (centers[:, None, :] + base[None, :, :]).reshape(-1, 2)
                d, idx = router.tree.query(pts)
                d = d.reshape(len(centers), -1)
                idx = idx.reshape(len(centers), -1)
                ok = (d.max(1) < min(180.0, 0.20 * s))
                sc = g.scenic[idx].mean(1)          # 꼭짓점이 공원·하천에 얼마나 닿나
                for ci in np.nonzero(ok)[0]:
                    # 스냅 거리는 반드시 모양 크기로 나눠서 비교합니다.
                    # 미터로 재면 작은 배치가 항상 이겨 상위를 다 차지합니다
                    # (260m 스케일을 넣자 나뭇잎 닮음이 55.7 → 35.8로 떨어졌습니다).
                    cands.append((float(d[ci].mean()) / s, s, rot, flip,
                                  float(centers[ci][0]), float(centers[ci][1]),
                                  idx[ci].tolist(), float(sc[ci])))
    cands.sort(key=lambda c: c[0])
    seen, uniq = set(), []          # 400m 격자 × 크기 × 각도 버킷당 하나만
    for c in cands:
        b = (round(c[4] / 400), round(c[5] / 400), c[1], c[2], c[3])
        if b in seen:
            continue
        seen.add(b)
        uniq.append(c)
    cands = uniq

    # 예산을 둘로 나눕니다.
    #  A: 그냥 잘 맞는 자리   B: 공원·하천에 걸친 자리
    # 하나로 합쳐 순위를 매기면 한쪽이 다른 쪽을 다 밀어냅니다. 산책로는
    # 선형(탄천)이거나 곡선 루프(중앙공원)라 격자보다 모양이 안 나오는데,
    # 그렇다고 빼버리면 정작 사람들이 뛰는 데는 코스가 하나도 안 생깁니다.
    # 크기별로 예산을 똑같이 나눕니다. 절대 미터로 재면 작은 배치가,
    # 크기로 나누면 큰 배치가 상위를 독식합니다. 어느 쪽으로 재도
    # 한쪽이 이기니, 크기마다 같은 수만큼 태워 보내는 게 공평합니다.
    def quota(pool, budget):
        cap = max(1, budget // len(SCALES))
        used, out = {}, []
        for c in pool:
            if used.get(c[1], 0) >= cap:
                continue
            used[c[1]] = used.get(c[1], 0) + 1
            out.append(c)
        return out

    half = ROUTE_TOP // 2
    pool_a = quota(cands, half)
    pool_b = quota([c for c in cands if c[7] >= SCENIC_MIN], half)
    seen_c, route_set = set(), []
    for c in pool_a + pool_b:
        k = (c[1], c[2], c[3], c[4], c[5])
        if k in seen_c:
            continue
        seen_c.add(k)
        route_set.append(c)
    if verbose:
        print(f"  {name}: 배치 후보 {len(cands)}개 → 경로 계산 {len(route_set)}개 "
              f"(일반 {len(pool_a)} + 공원하천 {len(pool_b)})")

    results = []
    for c in route_set:
        _, s, rot, flip, cx, cy, snap, _sc = c
        seq = list(dict.fromkeys(snap))          # 같은 노드로 뭉친 건 합침
        if len(seq) < 8:
            continue
        ideal = place(dense_ideal, cx, cy, s, rot, flip)
        off = cKDTree(ideal).query(g.xy)[0] / s      # 외곽선까지 거리(반폭 기준)
        legs, bad = [], False
        for a, b in zip(seq, seq[1:] + seq[:1]):
            p, _ = router.path(a, b, off, OFF_K)
            if p is None:
                bad = True
                break
            legs.append(p)
        if bad:
            continue
        r = evaluate(g, router, ideal, legs)
        if r:
            r.update(shape=key, name=name, scale=s, rot=rot, flip=flip,
                     cx=cx, cy=cy)
            results.append(r)
    results.sort(key=lambda r: -r["score"])

    def take(pool, n, taken):
        out = []
        for r in pool:
            b = (round(r["cx"] / 600), round(r["cy"] / 600))
            if b in taken:
                continue
            taken.add(b)
            out.append(r)
            if len(out) >= n:
                break
        return out

    # 공원·하천 코스 자리를 따로 떼어 둡니다. 점수만으로 자르면
    # 닮음이 높은 주택가 코스가 목록을 다 차지합니다.
    taken = set()
    scenic = take([r for r in results if r["scenic_r"] >= SCENIC_MIN],
                  KEEP_SCENIC, taken)
    rest = take(results, KEEP - len(scenic), taken)
    return sorted(scenic + rest, key=lambda r: -r["score"])


def main():
    t0 = time.time()
    g = Graph(load())
    router = Router(g)
    print("그래프", g.stats())
    out = []
    only = sys.argv[1:] or list(SHAPES)
    for key in only:
        name, shape = SHAPES[key]
        res = search(g, router, key, name, shape)
        for r in res:
            print(f"    {name} 종합 {r['score']:5.1f} · 닮음 {r['fidelity']:5.1f} · "
                  f"러닝 {r['run']:5.1f} | {r['km']:5.2f}km 오르막 {r['climb']:3.0f}m "
                  f"신호 {r['signals']:4.1f} 계단 {r['steps_m']:3.0f}m "
                  f"큰길 {r['major_r']:.0%} 되돌아옴 {r['repeat']:.0%} "
                  f"공원하천 {r['scenic_r']:.0%}")
        out.append((key, res))
    print(f"총 {time.time()-t0:.0f}초")
    return g, out


if __name__ == "__main__":
    main()
