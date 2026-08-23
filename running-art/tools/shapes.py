# -*- coding: utf-8 -*-
"""러닝 아트 모양 템플릿.

하나의 닫힌 외곽선만 씁니다 — 코스는 끊기지 않고 이어져야 하니까요.
게 다리처럼 가는 디테일은 넣지 않습니다. 1km 스케일에서 도로가 그걸
표현할 해상도가 없어서, 넣어봐야 뭉개진 선으로 나옵니다.
좌표는 정규화(가장 긴 축이 -1~1)해서 돌려줍니다.
"""
import math
import numpy as np

D = math.pi / 180.0


def _norm(pts):
    p = np.asarray(pts, dtype=np.float64)
    p = p - (p.min(0) + p.max(0)) / 2.0
    p /= np.abs(p).max()
    return p


def _ell(a0, a1, rx, ry, n=24, cx=0.0, cy=0.0):
    """타원 호. 각도는 도(度), a0 → a1 방향 그대로 진행."""
    t = np.linspace(a0 * D, a1 * D, n)
    return np.stack([cx + rx * np.cos(t), cy + ry * np.sin(t)], 1)


def star(k=5, inner=0.42):
    pts = []
    for i in range(k * 2):
        r = 1.0 if i % 2 == 0 else inner
        a = math.pi / 2 + i * math.pi / k
        pts.append((r * math.cos(a), r * math.sin(a)))
    return _norm(pts)


def heart():
    t = np.linspace(0, 2 * math.pi, 120)
    x = 16 * np.sin(t) ** 3
    y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
    return _norm(np.stack([x, y], 1))


def fish():
    body = _ell(-150, 150, 1.0, 0.62, 46)            # 몸통
    tail = np.array([[-0.86, 0.31], [-1.5, 0.75], [-1.5, -0.75], [-0.86, -0.31]])
    return _norm(np.vstack([body, tail]))


def whale():
    back = _ell(0, 180, 1.0, 0.50, 30)               # 등 (오른쪽 → 왼쪽)
    tail = np.array([[-1.22, 0.42], [-1.55, 0.58],   # 꼬리지느러미
                     [-1.30, 0.04], [-1.55, -0.52], [-1.22, -0.38]])
    belly = _ell(180, 360, 1.0, 0.38, 26)            # 배 (왼쪽 → 오른쪽)
    return _norm(np.vstack([back, tail, belly]))


def dolphin():
    back_a = _ell(0, 78, 1.0, 0.42, 12)              # 등: 머리 쪽
    fin = np.array([[0.22, 0.45], [0.02, 0.92], [-0.28, 0.40]])   # 등지느러미
    back_b = _ell(105, 180, 1.0, 0.42, 12)           # 등: 꼬리 쪽
    tail = np.array([[-1.20, 0.34], [-1.50, 0.46],
                     [-1.28, 0.02], [-1.50, -0.44], [-1.20, -0.32]])
    belly = _ell(180, 360, 1.0, 0.30, 24)
    return _norm(np.vstack([back_a, fin, back_b, tail, belly]))


def turtle():
    """등딱지 타원에 네 다리와 머리를 봉긋하게 얹는다."""
    lobes = [42, 138, 222, 318, 0]                   # 다리 넷 + 머리(0°)
    a = np.arange(0, 360, 3.0)
    r = np.ones_like(a)
    for lo in lobes:
        d = np.abs((a - lo + 180) % 360 - 180)
        r += 0.46 * np.exp(-(d / 6.5) ** 2)
    return _norm(np.stack([r * np.cos(a * D), r * np.sin(a * D) * 0.86], 1))


def moon():
    # 두 원이 뿔끝에서 정확히 만나야 선이 안 엇갈린다.
    tip, dx = 70.0, 0.55
    tx, ty = math.cos(tip * D), math.sin(tip * D)
    r2 = math.hypot(tx - dx, ty)
    t2 = math.degrees(math.atan2(ty, tx - dx))
    outer = _ell(tip, 360 - tip, 1.0, 1.0, 34)          # 바깥 원호
    inner = _ell(360 - t2, t2, r2, r2, 26, cx=dx)       # 안쪽을 파내는 호 (왼쪽으로 부풀려 도려냄)
    return _norm(np.vstack([outer, inner]))


def leaf():
    """두 호가 (±1, 0)에서 정확히 만나는 렌즈 모양."""
    R, c = 1.6, math.sqrt(1.6 ** 2 - 1)
    t = math.degrees(math.asin(c / R))
    up = _ell(t, 180 - t, R, R, 30, cy=-c)
    dn = _ell(180 + t, 360 - t, R, R, 30, cy=c)
    return _norm(np.vstack([up, dn]))


def drop():
    body = _ell(135, 405, 0.85, 0.85, 40, cy=-0.2)   # 위쪽만 트인 원
    tip = np.array([[0.0, 1.30]])                    # 뾰족한 끝
    return _norm(np.vstack([body, tip]))


def house():
    return _norm([[-1, -1], [1, -1], [1, 0.2], [0, 1], [-1, 0.2]])


def cat():
    head = _ell(118, 360 + 62, 1.0, 1.0, 40)         # 귀 자리만 비운 얼굴
    ears = np.array([[0.60, 0.98], [0.74, 1.42], [0.22, 1.00],   # 오른쪽 귀
                     [0.00, 0.90],
                     [-0.22, 1.00], [-0.74, 1.42], [-0.60, 0.98]])  # 왼쪽 귀
    return _norm(np.vstack([head, ears]))


SHAPES = {
    "star":    ("별",     star()),
    "heart":   ("하트",   heart()),
    "fish":    ("물고기", fish()),
    "whale":   ("고래",   whale()),
    "dolphin": ("돌고래", dolphin()),
    "turtle":  ("거북이", turtle()),
    "moon":    ("초승달", moon()),
    "leaf":    ("나뭇잎", leaf()),
    "drop":    ("물방울", drop()),
    "house":   ("집",     house()),
    "cat":     ("고양이", cat()),
}


def resample(pts, n):
    """닫힌 외곽선을 등간격 n점으로 다시 뽑는다."""
    p = np.vstack([pts, pts[:1]])
    seg = np.hypot(*(p[1:] - p[:-1]).T)
    cum = np.concatenate([[0], np.cumsum(seg)])
    t = np.linspace(0, cum[-1], n, endpoint=False)
    return np.stack([np.interp(t, cum, p[:, 0]), np.interp(t, cum, p[:, 1])], 1)


if __name__ == "__main__":
    for k, (ko, p) in SHAPES.items():
        print(f"{k:8s} {ko:4s} 점 {len(p):3d}")
