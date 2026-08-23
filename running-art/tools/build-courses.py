# -*- coding: utf-8 -*-
"""모양별 최적 코스를 찾아 data/courses.json 으로 저장.

자동 생성 파일입니다. data/courses.json 을 직접 고치지 마세요.
"""
import json, math, os, sys, time, datetime
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match as M
from graph import Graph, load
from shapes import SHAPES

from region import NAME, DESC

REGION = f"{NAME} — {DESC}"


def main():
    t0 = time.time()
    raw = load()
    g = Graph(raw)
    router = M.Router(g)
    print("그래프", g.stats())

    courses = []
    for key, (name, shape) in SHAPES.items():
        res = M.search(g, router, key, name, shape)
        seen = set()
        for r in res:
            sig = (round(r["score"], 1), r["km"])      # 같은 자리 중복 제거
            if sig in seen:
                continue
            seen.add(sig)
            xy = g.xy[r["path"]]
            coords = [[round(v, 6) for v in g.to_ll(x, y)] for x, y in xy]
            courses.append(dict(
                shape=key, name=name, score=r["score"], fidelity=r["fidelity"],
                run=r["run"], km=r["km"], repeat=r["repeat"], comfort=r["comfort"],
                climb=r["climb"], climb_km=r["climb_km"], signals=r["signals"],
                scenic_r=r["scenic_r"],
                signal_km=r["signal_km"], steps_m=r["steps_m"], major_r=r["major_r"],
                scale=r["scale"], rot=r["rot"], flip=r["flip"],
                center=[round(v, 6) for v in g.to_ll(r["cx"], r["cy"])],
                coords=coords,
            ))
        print(f"  {name}: {len([c for c in courses if c['shape']==key])}개")

    courses.sort(key=lambda c: -c["score"])

    # 포스터 뷰용: 서로 안 겹치게 자리를 나눠 갖는 한 벌을 고른다.
    # 점수만 보고 고르면 길 좋은 한 구역에 모양이 전부 포개진다.
    lat0 = math.radians(g.center[0])
    mx, my = 111320 * math.cos(lat0), 110540
    chosen, used_shape = [], set()
    for c in courses:
        if c["shape"] in used_shape:
            continue
        cx = (c["center"][1] - g.center[1]) * mx
        cy = (c["center"][0] - g.center[0]) * my
        if any(math.hypot(cx - ox, cy - oy) < (c["scale"] + os_) * 0.85
               for ox, oy, os_ in chosen):
            continue
        chosen.append((cx, cy, c["scale"]))
        used_shape.add(c["shape"])
        c["poster"] = True
    print(f"  포스터 배치 {len(chosen)}개 / 모양 {len(SHAPES)}개")
    # 목록에 그릴 원본 모양 아이콘도 같이 내보낸다
    icons = {k: dict(name=n, pts=[[round(float(x), 4), round(float(y), 4)] for x, y in p])
             for k, (n, p) in SHAPES.items()}
    out = dict(
        region=REGION,
        icons=icons,
        center=list(g.center),
        radius=g.radius,
        generated=datetime.date.today().isoformat(),
        roads_km=g.stats()["km"],
        courses=courses,
    )
    # 출력은 모양당 4개까지만. 탐색은 넓게 하되 파일은 가볍게 유지합니다.
    keep, cnt = [], {}
    for c in courses:
        n = cnt.get(c["shape"], 0)
        if n < 4 or c.get("poster"):
            keep.append(c)
            cnt[c["shape"]] = n + 1
    courses = keep
    out["courses"] = courses

    path = os.path.join(os.path.dirname(__file__), "..", "data", "courses.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"코스 {len(courses)}개 → {os.path.abspath(path)} "
          f"{os.path.getsize(path)//1024}KB, {time.time()-t0:.0f}초")


if __name__ == "__main__":
    main()
