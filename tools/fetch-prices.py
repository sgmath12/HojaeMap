#!/usr/bin/env python3
"""국토교통부 아파트 매매 실거래가를 받아 data-prices.js를 만든다.

⚠️ 이 스크립트는 실행 검증이 되지 않았습니다. data.go.kr 인증키가 있어야
   호출이 가능한데 작성 시점에 키가 없었습니다. 처음 돌릴 때는 --dry-run으로
   요청 형태를 먼저 확인하세요.

준비:
  1. https://www.data.go.kr 가입
  2. "국토교통부_아파트 매매 실거래가 상세 자료" 활용신청 (자동 승인)
  3. "행정안전부_행정표준코드 행정구역코드" 활용신청 — 법정동코드 매핑용
  4. 마이페이지에서 일반 인증키(Decoding) 복사

사용:
  export MOLIT_API_KEY='발급받은키'
  python3 tools/fetch-prices.py --dry-run       # 호출 형태만 출력
  python3 tools/fetch-prices.py --months 6      # 최근 6개월
  python3 tools/fetch-prices.py --months 6 --sido 11 31   # 서울·경기만

왜 매핑이 필요한가:
  이 지도의 경계는 통계청 '행정동' 코드(7자리)를 쓰는데, 실거래가 API는
  행정안전부 '법정동' 코드(LAWD_CD 5자리)를 씁니다. 둘은 체계가 다릅니다.
    통계청 11230 강남구  ↔  법정동 11680 강남구
  게다가 행정동과 법정동은 1:1이 아닙니다. '대치1동'(행정동)은 '대치동'
  (법정동)의 일부고, 반대로 한 행정동이 여러 법정동을 걸치기도 합니다.
  그래서 이 스크립트는 두 체계를 이름으로 맞춰(geo-sigungu.json 의 시군구명 ↔
  행정표준코드의 주소명) 결과를 **통계청 코드 기준**으로 내보냅니다. 동 단위는
  법정동명을 그대로 붙여두고, 앱에서 이름을 느슨하게 이어 씁니다. 이어지지
  않으면 시군구 중앙값으로 떨어집니다.
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", "cache")

STANREGIN = "https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList"
APT_TRADE = ("https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/"
             "getRTMSDataSvcAptTradeDev")

# 통계청 시도코드 → 법정동 시도코드
SIDO_MAP = {
    "11": "11", "21": "26", "22": "27", "23": "28", "24": "29",
    "25": "30", "26": "31", "29": "36", "31": "41", "32": "51",
    "33": "43", "34": "44", "35": "52", "36": "46", "37": "47",
    "38": "48", "39": "50",
}


def get(url, params, retries=3):
    q = urllib.parse.urlencode(params, safe="")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=40) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def load_lawd(key, refresh=False):
    """법정동 시군구 코드 목록 [(LAWD_CD, 시도명, 시군구명)] 을 만든다."""
    path = os.path.join(CACHE, "lawd.json")
    if os.path.exists(path) and not refresh:
        return json.load(open(path, encoding="utf-8"))

    os.makedirs(CACHE, exist_ok=True)
    rows, page = [], 1
    while True:
        raw = get(STANREGIN, {
            "ServiceKey": key, "type": "json",
            "pageNo": page, "numOfRows": 1000,
        })
        data = json.loads(raw)
        body = data.get("StanReginCd")
        if not body or len(body) < 2:
            break
        items = body[1].get("row", [])
        if not items:
            break
        for it in items:
            code = str(it.get("region_cd", ""))
            # 시군구 단위만: 앞 5자리가 유효하고 뒤 5자리가 00000
            if len(code) == 10 and code[5:] == "00000" and code[2:5] != "000":
                rows.append([code[:5], it.get("locatadd_nm", "")])
        page += 1
        if page > 60:
            break

    json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return rows


def map_to_kostat(lawd):
    """법정동 시군구코드 → 통계청 시군구코드. 주소명을 공백 제거 후 부분일치."""
    topo = json.load(open(os.path.join(ROOT, "geo-sigungu.json"), encoding="utf-8"))
    key = list(topo["objects"].keys())[0]
    ks = [(str(g["properties"]["code"]), g["properties"]["name"])
          for g in topo["objects"][key]["geometries"]]

    out, used = {}, set()
    for lcode, addr in lawd:
        flat = addr.replace(" ", "")
        best = None
        for kcode, kname in ks:
            if kcode in used:
                continue
            if kname and kname in flat:
                if best is None or len(kname) > len(best[1]):
                    best = (kcode, kname)
        if best:
            out[lcode] = best[0]
            used.add(best[0])
    return out


def fetch_month(key, lawd, ym):
    """한 시군구·한 달 거래 목록."""
    raw = get(APT_TRADE, {
        "serviceKey": key, "LAWD_CD": lawd, "DEAL_YMD": ym,
        "numOfRows": 2000, "pageNo": 1, "_type": "json",
    })
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []                                   # 오류 시 XML이 온다
    body = (data.get("response") or {}).get("body") or {}
    items = (body.get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items


def months_back(n):
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("MOLIT_API_KEY"))
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--sido", nargs="*", help="통계청 시도코드 (예: 11 31)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-lawd", action="store_true")
    args = ap.parse_args()

    yms = months_back(args.months)

    if args.dry_run:
        print("호출 형태 (키는 가림):\n")
        print(f"  법정동코드:  {STANREGIN}?ServiceKey=***&type=json&pageNo=1&numOfRows=1000")
        print(f"  실거래가:    {APT_TRADE}?serviceKey=***&LAWD_CD=11680&DEAL_YMD={yms[0]}"
              f"&numOfRows=2000&pageNo=1&_type=json")
        print(f"\n대상 기간: {', '.join(yms)}")
        print("\n실행하려면 --dry-run 을 빼고 MOLIT_API_KEY 를 설정하세요.")
        return

    if not args.key:
        sys.exit("인증키가 없습니다. MOLIT_API_KEY 를 설정하거나 --key 로 넘기세요.")

    print("법정동 시군구 코드 목록 만드는 중...")
    lawd = load_lawd(args.key, args.refresh_lawd)
    if args.sido:
        want = {SIDO_MAP[s] for s in args.sido if s in SIDO_MAP}
        lawd = [r for r in lawd if r[0][:2] in want]
    kostat = map_to_kostat(lawd)
    print(f"  시군구 {len(lawd)}개 · 통계청 코드로 매칭된 곳 {len(kostat)}개")
    unmatched = [n for c, n in lawd if c not in kostat]
    if unmatched:
        print(f"  ⚠️ 매칭 실패 {len(unmatched)}곳 — 결과에서 빠집니다: {', '.join(unmatched[:5])}...")
    print(f"  {len(yms)}개월 = 호출 {len(lawd) * len(yms)}회")

    # (LAWD_CD, 법정동명) → 단위면적당 가격 리스트
    per_dong = defaultdict(list)
    per_sgg = defaultdict(list)

    for i, (code, name) in enumerate(lawd, 1):
        for ym in yms:
            for it in fetch_month(args.key, code, ym):
                try:
                    amount = int(str(it.get("dealAmount", "")).replace(",", "").strip())
                    area = float(it.get("excluUseAr"))
                    dong = str(it.get("umdNm", "")).strip()
                except (TypeError, ValueError):
                    continue
                if area <= 0 or amount <= 0:
                    continue
                per_m2 = amount * 10000 / area          # 만원 단위 → 원/㎡
                kc = kostat.get(code)
                if not kc:
                    continue
                per_dong[(kc, dong)].append(per_m2)
                per_sgg[kc].append(per_m2)
            time.sleep(0.12)                            # 호출 간격
        if i % 20 == 0:
            print(f"  {i}/{len(lawd)} {name}")

    rows = {}
    for (code, dong), v in per_dong.items():
        if len(v) < 3:                                  # 표본 3건 미만은 버림
            continue
        rows[f"{code}|{dong}"] = {
            "won_per_m2": int(statistics.median(v)),
            "won_per_py": int(statistics.median(v) * 3.3058),
            "n": len(v),
        }
    sgg = {c: {"won_per_m2": int(statistics.median(v)),
               "won_per_py": int(statistics.median(v) * 3.3058),
               "n": len(v)}
           for c, v in per_sgg.items() if len(v) >= 5}

    out = os.path.join(ROOT, "data-prices.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("// 자동 생성 — 직접 고치지 마세요.\n")
        f.write("// 생성: python3 tools/fetch-prices.py\n")
        f.write("// 출처: 국토교통부 아파트 매매 실거래가 (data.go.kr)\n")
        f.write(f"// 기간: {yms[-1]} ~ {yms[0]} · 값은 전용면적 기준 중앙값\n")
        f.write("// 키 형식: PRICES 는 \"통계청시군구코드|법정동명\", PRICES_SGG 는 \"통계청시군구코드\"\n")
        f.write("const PRICES = ")
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\nconst PRICES_SGG = ")
        json.dump(sgg, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print(f"\n{out} 생성 — 법정동 {len(rows)}개 · 시군구 {len(sgg)}개")


if __name__ == "__main__":
    main()
