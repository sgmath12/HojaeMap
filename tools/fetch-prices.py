#!/usr/bin/env python3
"""국토교통부 아파트 매매 실거래가를 받아 data-prices.js를 만든다.

  국토교통부 실거래가 정보 오픈API — 아파트 매매 실거래가 자료
  https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade
  응답은 XML. 명세는 data/아파트 매매 실거래가 자료 기술문서.pdf

인증키는 data/api_key.md 에서 읽는다(git에 올라가지 않음). 환경변수
MOLIT_API_KEY 로 덮어쓸 수 있다.

  python3 tools/fetch-prices.py --verify              # 코드표 검증만
  python3 tools/fetch-prices.py --months 3 --sido 11 31
  python3 tools/fetch-prices.py --months 6            # 전국

⚠️ 해제된 거래(cdealType='O')는 뺀다. 계약이 취소된 건이라 시세가 아니다.
   가격은 평균이 아니라 **중앙값**을 쓴다. 초고가 펜트하우스 한 건이
   동네 대표값을 흔드는 것을 막기 위해서다.

⚠️ 이 API는 법정동 코드(LAWD_CD)를 쓰고 이 지도는 통계청 코드를 쓴다.
   둘은 번호 체계가 완전히 다르다 (종로구: 법정동 11110 ↔ 통계청 11010).
   tools/lawd.py 의 표로 이름을 맞춰 잇는다.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lawd import LAWD, SIDO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
PY_PER_M2 = 3.3058          # 1평 = 3.3058㎡


def read_key():
    if os.environ.get("MOLIT_API_KEY"):
        return os.environ["MOLIT_API_KEY"].strip()
    for p in [os.path.join(ROOT, "data", "api_key.md"),
              os.path.expanduser("~/.hojae_api_key.md")]:
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8").read()
        m = re.search(r"일반 인증키\s*\n\s*(\S+)", txt)
        if m:
            return m.group(1).strip()
    sys.exit("인증키를 찾지 못했습니다. data/api_key.md 를 두거나 MOLIT_API_KEY 를 설정하세요.")


def fetch(key, lawd, ym, rows=1000, retries=3):
    """한 시군구·한 달 거래 목록. 키는 이미 URL 인코딩되어 있어 그대로 붙인다."""
    url = (f"{URL}?serviceKey={key}&LAWD_CD={lawd}&DEAL_YMD={ym}"
           f"&pageNo=1&numOfRows={rows}")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                xml = r.read().decode("utf-8")
            break
        except Exception:
            if attempt == retries - 1:
                return [], None
            time.sleep(1.5 * (attempt + 1))
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [], None
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("000", "00"):
        msg = (root.findtext(".//resultMsg") or "").strip()
        return [], f"{code} {msg}"
    return root.findall(".//item"), None


def text(item, tag):
    v = item.findtext(tag)
    return (v or "").strip()


def months_back(n):
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out


def build_code_map():
    """법정동 시군구 코드 → 통계청 시군구 코드."""
    topo = json.load(open(os.path.join(ROOT, "geo-sigungu.json"), encoding="utf-8"))
    key = list(topo["objects"].keys())[0]
    geo = [(str(g["properties"]["code"]), g["properties"]["name"])
           for g in topo["objects"][key]["geometries"]]

    out, missed = {}, []
    for lcode, lname in LAWD.items():
        ks = SIDO.get(lcode[:2])
        if not ks:
            missed.append((lcode, lname, "시도 미매핑"))
            continue
        pool = [(c, n) for c, n in geo if c.startswith(ks)]
        hit = [c for c, n in pool if n == lname]
        if not hit:      # 세종특별자치시 ↔ 세종시, 미추홀구 ↔ 남구 등
            alias = {"세종특별자치시": "세종시", "미추홀구": "남구"}.get(lname)
            if alias:
                hit = [c for c, n in pool if n == alias]
        if not hit:      # 폐지된 구(부천시원미구) → 상위 시
            base = re.sub(r"시[가-힣]+구$", "시", lname)
            hit = [c for c, n in pool if n == base]
        if hit:
            out[lcode] = hit[0]
        else:
            missed.append((lcode, lname, "시군구 미매칭"))
    return out, missed, dict(geo)


def verify(key, cmap, geo, sample_ym):
    """응답의 sggCd로 코드표를 검증한다.

    주의: estateAgentSggNm(중개사 소재지)으로는 검증할 수 없다. 중개사 위치는
    매물 위치와 다를 수 있어서(부산 중구 매물을 동구 중개사가 거래) 멀쩡한
    코드가 틀린 것처럼 보인다. 실제로 그렇게 오판했었다.
    """
    print(f"코드표 검증 ({sample_ym}) — 거래가 있는 시군구만 확인 가능\n")
    ok = bad = nodata = 0
    for lcode in sorted(LAWD):
        if lcode not in cmap:
            continue
        items, err = fetch(key, lcode, sample_ym, rows=1)
        if err:
            print(f"  !! {lcode} {LAWD[lcode]}: {err}")
            bad += 1
            continue
        if not items:
            nodata += 1
            continue
        got = text(items[0], "sggCd")
        if not got:
            nodata += 1
            continue
        if got == lcode:
            ok += 1
        else:
            print(f"  !! {lcode} {LAWD[lcode]} → API가 돌려준 sggCd={got}")
            bad += 1
        time.sleep(0.05)
    print(f"\n  일치 {ok} · 불일치 {bad} · 거래없어 확인불가 {nodata}")
    return bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--sido", nargs="*", help="통계청 시도코드 (예: 11 31)")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    key = read_key()
    cmap, missed, geo = build_code_map()
    print(f"코드 매핑 {len(cmap)}/{len(LAWD)}곳")
    for lc, ln, why in missed:
        print(f"  - {lc} {ln}: {why}")

    yms = months_back(args.months)

    if args.verify:
        sys.exit(0 if verify(key, cmap, geo, yms[0]) else 1)

    targets = sorted(cmap)
    if args.sido:
        want = set(args.sido)
        targets = [l for l in targets if cmap[l][:2] in want]
    print(f"대상 {len(targets)}곳 × {len(yms)}개월 = {len(targets)*len(yms)}회 호출\n")

    per_dong = defaultdict(list)      # (통계청시군구, 법정동명) → 평당가 목록
    per_sgg = defaultdict(list)
    n_deal = n_drop = 0
    errors = []

    for i, lcode in enumerate(targets, 1):
        kcode = cmap[lcode]
        for ym in yms:
            items, err = fetch(key, lcode, ym)
            if err:
                errors.append(f"{LAWD[lcode]} {ym}: {err}")
                continue
            for it in items:
                # 해제된 거래는 시세가 아니다
                if text(it, "cdealType") == "O":
                    n_drop += 1
                    continue
                try:
                    amount = int(text(it, "dealAmount").replace(",", ""))
                    area = float(text(it, "excluUseAr"))
                except ValueError:
                    continue
                if amount <= 0 or area <= 0:
                    continue
                per_py = amount * 10000 / area * PY_PER_M2
                dong = text(it, "umdNm")
                per_sgg[kcode].append(per_py)
                if dong:
                    per_dong[(kcode, dong)].append(per_py)
                n_deal += 1
            time.sleep(0.06)
        if i % 25 == 0 or i == len(targets):
            print(f"  {i}/{len(targets)} · 거래 {n_deal:,}건")

    def summarize(vals, least):
        if len(vals) < least:
            return None
        return {"py": int(statistics.median(vals)), "n": len(vals)}

    prices = {}
    for (kcode, dong), v in per_dong.items():
        s = summarize(v, 3)
        if s:
            prices[f"{kcode}|{dong}"] = s
    sgg = {c: s for c, v in per_sgg.items() if (s := summarize(v, 5))}

    # 전국 순위 — 카드에서 "전국 상위 N%" 로 쓴다
    ranked = sorted(sgg.items(), key=lambda kv: -kv[1]["py"])
    for rank, (c, s) in enumerate(ranked, 1):
        s["rank"] = rank
    total = len(ranked)

    out = os.path.join(ROOT, "data-prices.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("// 자동 생성 — 직접 고치지 마세요.\n")
        f.write("// 생성: python3 tools/fetch-prices.py\n")
        f.write("// 출처: 국토교통부 아파트 매매 실거래가 오픈API (data.go.kr)\n")
        f.write(f"// 기간: {yms[-1]} ~ {yms[0]} · 해제 거래 제외 · 값은 평당가 중앙값(원)\n")
        f.write("//\n")
        f.write("// PRICES     \"통계청시군구코드|법정동명\" → {py:평당가, n:거래건수}\n")
        f.write("// PRICES_SGG \"통계청시군구코드\" → {py, n, rank}\n")
        f.write(f"const PRICE_META = {json.dumps({'from':yms[-1],'to':yms[0],'total_sgg':total,'deals':n_deal}, ensure_ascii=False)};\n")
        f.write("const PRICES = ")
        json.dump(prices, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\nconst PRICES_SGG = ")
        json.dump(sgg, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print(f"\n{out}")
    print(f"  거래 {n_deal:,}건 (해제 제외 {n_drop}건) · 시군구 {len(sgg)}곳 · 법정동 {len(prices)}곳")
    if errors:
        print(f"  오류 {len(errors)}건: {errors[:3]}")


if __name__ == "__main__":
    main()
