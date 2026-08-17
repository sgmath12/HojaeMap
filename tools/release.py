#!/usr/bin/env python3
"""배포 준비 — 버전을 올리고 index.html·version.json을 맞춘다.

GitHub Pages는 응답 헤더를 우리가 정할 수 없다. index.html이 `max-age=600`으로
내려오고, 모바일 브라우저는 그보다 오래 붙들기도 한다. 그래서 파일 이름에
해시를 붙이는 흔한 방법(`app.a1b2c3.js`)을 index.html 자체에는 쓸 수 없다.

대신 페이지가 스스로 확인하게 한다.
  1. 빌드 시각을 APP_VERSION 으로 index.html 안에 박아두고
  2. 같은 값을 version.json 에 따로 쓴다
  3. 페이지는 뜨자마자 version.json 을 no-store 로 받아 자기 버전과 비교하고
  4. 다르면 ?v=<새버전> 으로 주소를 바꿔 다시 연다 (새 URL이라 캐시를 비껴간다)

version.json 은 100바이트 남짓이라 매번 새로 받아도 부담이 없다.

  python3 tools/release.py            # 버전 = 현재 시각
  python3 tools/release.py 2026.08.18 # 버전 직접 지정
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "realestate-map_new.html")
OUT = os.path.join(ROOT, "index.html")
VER = os.path.join(ROOT, "version.json")

ASSETS = ["data-regions.js", "data-metrics.js", "data-redev.js", "data-prices.js"]


def check_html(html):
    """div 짝이 맞는지 확인.

    </div> 하나가 남아 #sidebar가 조기 종료되면서 사이드바 전체가 가로로
    펼쳐진 적이 있다. JS 문법 검사로는 안 잡히는 종류라 여기서 막는다.
    """
    body = html[html.index("<body>") + 6: html.index('<script src="https://unpkg.com/leaflet')]
    depth = 0
    for m in re.finditer(r"<(/?)div\b[^>]*?(/?)>", body):
        if m.group(1) == "/":
            depth -= 1
        elif not m.group(2):
            depth += 1
    if depth != 0:
        sys.exit(f"!! div 짝이 맞지 않습니다 (균형 {depth}). 배포를 멈춥니다.")

    # 사이드바가 주요 블록을 다 품고 있는지 (검색은 지도 위 오버레이라 제외)
    seg = html[html.index('<div id="sidebar">'): html.index("</body>")]
    missing = [k for k in ("legend", "filters", "detail") if f'id="{k}"' not in seg]
    if missing:
        sys.exit(f"!! 사이드바 밖으로 빠진 요소: {missing}. 배포를 멈춥니다.")

    # 검색은 지도 영역 안에 있어야 오버레이로 뜬다
    mapseg = html[html.index('<div id="map-area">'): html.index('<div id="sidebar">')]
    if 'id="search-box"' not in mapseg:
        sys.exit("!! 검색창이 지도 영역 밖에 있습니다. 배포를 멈춥니다.")

    # JS가 없는 id를 찾지 않는지
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r'getElementById\("([^"]+)"\)', html))
    ghost = used - ids
    if ghost:
        sys.exit(f"!! HTML에 없는 id를 참조합니다: {ghost}. 배포를 멈춥니다.")


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y.%m.%d.%H%M")
    html = open(SRC, encoding="utf-8").read()

    check_html(html)

    html = re.sub(r'const APP_VERSION = "[^"]*"',
                  f'const APP_VERSION = "v{version}"', html)
    tag = version.replace(".", "")
    for a in ASSETS:
        html = re.sub(rf'src="{re.escape(a)}(\?v=[^"]*)?"', f'src="{a}?v={tag}"', html)

    open(SRC, "w", encoding="utf-8").write(html)
    open(OUT, "w", encoding="utf-8").write(html)
    json.dump({"version": f"v{version}"}, open(VER, "w", encoding="utf-8"))

    # 문법 확인
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    tmp = os.path.join(ROOT, ".release-check.js")
    open(tmp, "w", encoding="utf-8").write(js)
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.remove(tmp)
    if r.returncode:
        sys.exit(f"!! JS 문법 오류\n{r.stderr}")

    print(f"v{version} 준비 완료")
    print("  index.html · version.json 갱신, 구조·문법 검사 통과")
    print("  이제 커밋하고 푸시하세요.")


if __name__ == "__main__":
    main()
