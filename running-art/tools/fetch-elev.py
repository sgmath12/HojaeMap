# -*- coding: utf-8 -*-
"""도로 노드의 고도를 받아 data/elev.json 으로 저장 (SRTM 30m).

오르막은 러닝 코스 체감을 가장 크게 바꾸는 요소인데 OSM에는 없습니다.
opentopodata 공개 서버는 요청당 100지점·초당 1회 제한이 있어, 30m 격자로
묶어서 받습니다(원본 해상도가 30m라 더 촘촘히 받아봐야 같은 값입니다).
"""
import json, os, sys, time, urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph import Graph, load

CELL = 0.0003          # 약 33m — SRTM 해상도와 맞춤
BATCH = 100
URL = "https://api.opentopodata.org/v1/srtm30m"


def main():
    g = Graph(load())
    cells = sorted({(round(la / CELL), round(lo / CELL))
                    for la, lo in (g.to_ll(x, y) for x, y in g.xy)})
    print(f"노드 {len(g.ids)}개 → 격자 {len(cells)}개 "
          f"({(len(cells)+BATCH-1)//BATCH}회 요청, 약 {len(cells)/BATCH*1.2/60:.1f}분)")

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "elev.json")
    done = {}
    if os.path.exists(out_path):                    # 중간에 끊겨도 이어받기
        done = json.load(open(out_path, encoding="utf-8"))
        print(f"  이미 받은 격자 {len(done)}개는 건너뜁니다")
    todo = [c for c in cells if f"{c[0]},{c[1]}" not in done]

    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        locs = "|".join(f"{a*CELL:.6f},{b*CELL:.6f}" for a, b in chunk)
        for attempt in range(4):
            try:
                url = URL + "?" + urllib.parse.urlencode({"locations": locs})
                with urllib.request.urlopen(url, timeout=60) as r:
                    res = json.load(r)["results"]
                break
            except Exception as e:
                print(f"  재시도 {attempt+1}: {e}")
                time.sleep(5)
        else:
            raise SystemExit("고도 API 응답 없음")
        for c, v in zip(chunk, res):
            done[f"{c[0]},{c[1]}"] = v["elevation"]
        if (i // BATCH) % 10 == 0:
            json.dump(done, open(out_path, "w"), separators=(",", ":"))
            print(f"  {i+len(chunk)}/{len(todo)}")
        time.sleep(1.2)

    json.dump(done, open(out_path, "w"), separators=(",", ":"))
    vs = [v for v in done.values() if v is not None]
    print(f"격자 {len(done)}개 저장. 고도 {min(vs):.0f}~{max(vs):.0f}m")


if __name__ == "__main__":
    main()
