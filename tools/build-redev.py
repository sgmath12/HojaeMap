#!/usr/bin/env python3
"""정비사업(재개발·재건축) 공공데이터를 모아 data-redev.js를 만든다.

data/ 안의 CSV를 읽어 시군구·행정동별로 집계한다. 사람 판단이 들어가는 곳은
단계별 가중치 하나뿐이고, 나머지는 전부 집계다.

입력 (data/):
  국토교통부_전국 도시정비사업 통합 데이터  — 전국 1,566건. 기준 데이터
  경기도_일반정비사업추진현황              — 위치·세대수 상세, 준공 포함
  인천광역시_도시 및 주거환경 정비사업       — 구·위치
  서울특별시_서울시 정비사업 데이터          — 법정동명이 있어 동 단위 매칭 가능

⚠️ 준공·청산은 제외한다. 이미 끝난 사업이라 앞으로의 호재가 아니다.
   (경기도 파일 533건 중 186건이 여기 해당한다)

⚠️ 규모는 구역 수가 아니라 **공급 예정 세대수**로 잰다. 구역 수로 세면
   종로구(70구역·3,678세대, 전부 상업지 도시환경정비)가 강남구(26구역·
   37,514세대, 전부 아파트 재건축)를 앞지른다. 주거 호재를 보는 지도에서는
   틀린 답이다.

⚠️ 단계 가중치는 "얼마나 확실하고 임박했는가"다. 착공은 거의 확정이고
   추진위 구성은 무산될 수도 있다.

  python3 tools/build-redev.py
"""

import csv
import json
import os
import re
import sys
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

SIDO = {
    "서울특별시": "11", "부산광역시": "21", "대구광역시": "22", "인천광역시": "23",
    "광주광역시": "24", "대전광역시": "25", "울산광역시": "26", "세종특별자치시": "29",
    "경기도": "31", "강원특별자치도": "32", "강원도": "32", "충청북도": "33",
    "충청남도": "34", "전북특별자치도": "35", "전라북도": "35", "전라남도": "36",
    "경상북도": "37", "경상남도": "38", "제주특별자치도": "39",
}
ALIAS = {"미추홀구": "남구", "세종특별자치시": "세종시"}

# 세대수가 비어 있을 때 쓸 유형별 대푯값(중앙값). 도시환경정비는 상업지
# 소규모라 작고, 아파트 재건축은 크다.
TYPE_UNITS = {"재건축": 900, "주택정비": 600, "주거환경": 300, "도시정비": 90}
DEFAULT_UNITS = 300


def guess_units(kind):
    for k, v in TYPE_UNITS.items():
        if k in (kind or ""):
            return v
    return DEFAULT_UNITS


# 단계 → (가중치, 표시명). 준공·청산은 0 — 이미 끝난 사업.
STAGE = [
    (("착공",),                     1.00, "착공"),
    (("관리처분",),                 0.90, "관리처분인가"),
    (("사업시행",),                 0.75, "사업시행인가"),
    (("조합설립",),                 0.55, "조합설립인가"),
    (("사업시행자지정", "지정통지"), 0.50, "사업시행자 지정"),
    (("정비구역", "구역지정"),      0.35, "정비구역 지정"),
    (("예정구역",),                 0.20, "예정구역"),
    (("추진위", "주민대표"),        0.25, "추진위 구성"),
    (("준공", "청산", "해제"),      0.00, "완료"),
]


def stage_of(text):
    t = re.sub(r"^\d+\)", "", (text or "").strip())
    for keys, w, label in STAGE:
        if any(k in t for k in keys):
            return w, label
    return 0.4, t or "진행중"


def load_geo(name):
    topo = json.load(open(os.path.join(ROOT, name), encoding="utf-8"))
    key = list(topo["objects"].keys())[0]
    return {str(g["properties"]["code"]): g["properties"]["name"]
            for g in topo["objects"][key]["geometries"]}


class Matcher:
    def __init__(self):
        self.sgg = load_geo("geo-sigungu.json")
        self.dong = load_geo("geo-dong.json")
        self.by_sido = defaultdict(dict)
        for c, n in self.sgg.items():
            self.by_sido[c[:2]][c] = n
        self.dong_by_sgg = defaultdict(dict)
        for c, n in self.dong.items():
            self.dong_by_sgg[c[:5]][c] = n

    def sigungu(self, sido_name, sgg_name):
        sd = SIDO.get((sido_name or "").strip())
        if not sd:
            return None
        s = ALIAS.get(sgg_name.strip(), sgg_name.strip())
        pool = self.by_sido[sd]
        for c, n in pool.items():
            # "안양시만안구" 와 "안양만안구" 를 같게 본다
            if n == s or n.replace("시", "", 1) == s:
                return c
        for c, n in pool.items():          # 폐지된 구 → 상위 시 (부천소사구)
            if n.endswith("시") and s.startswith(n[:-1]):
                return c
        return None

    def dong_in(self, sgg_code, dong_name):
        """법정동명 → 행정동 코드들. 대치동 → 대치1·2·4동 (1:N).

        법정동과 행정동은 이름이 어긋나는 경우가 많다. 도심(서소문동→소공동)이나
        통합된 곳(용두동→용신동)은 이름만으로는 이을 수 없다. 억지로 잇지 않고
        비워두는 쪽을 택한다 — 틀린 곳에 표시하느니 표시하지 않는 게 낫다.
        """
        if not sgg_code or not dong_name:
            return []
        base = dong_name.strip()
        if not base.endswith(("동", "가")):
            return []
        pool = self.dong_by_sgg[sgg_code]

        if base.endswith("동"):
            stem = base[:-1]
            exact = [c for c, n in pool.items()
                     if n == base or re.fullmatch(re.escape(stem) + r"[0-9·]*동", n)]
            if exact:
                return exact
        else:
            # "성수동2가", "을지로3가" → 접두 "성수", "을지로"
            stem = re.sub(r"동?\d*가$", "", base)

        # 접두 일치 (성수 → 성수1가1동·성수2가3동). 2글자 미만은 오탐이 커서 제외
        if len(stem) >= 2:
            return [c for c, n in pool.items() if n.startswith(stem)]
        return []


def parse_addr(addr):
    """'경기도 고양시 덕양구 관산동 178-57번지' → (시도, 시군구, 법정동)"""
    if not addr:
        return None, None, None
    toks = addr.split()
    sido = toks[0] if toks and toks[0] in SIDO else None
    sgg = dong = None
    for t in toks[1:]:
        if t.endswith(("시", "군")) and not sgg:
            sgg = t
        elif t.endswith("구"):
            sgg = (sgg[:-1] if sgg and sgg.endswith("시") else (sgg or "")) + t if sgg else t
        elif t.endswith("동") and not dong:
            dong = t
    return sido, sgg, dong


def main():
    M = Matcher()
    # code → {"n":활성 구역 수, "w":가중합, "units":세대수, "stages":{라벨:수}}
    sgg_acc = defaultdict(lambda: {"n": 0, "w": 0.0, "units": 0, "stages": defaultdict(int)})
    dong_acc = defaultdict(lambda: {"n": 0, "w": 0.0, "units": 0, "stages": defaultdict(int)})
    seen_done = 0

    def add(code, w, label, units, dongs=()):
        """w=단계 가중치, units=공급 예정 세대수. 규모는 세대수로 잰다."""
        nonlocal seen_done
        if w <= 0:
            seen_done += 1
            return
        a = sgg_acc[code]
        a["n"] += 1
        a["w"] += w * units
        a["units"] += units
        a["stages"][label] += 1
        # 한 구역이 여러 행정동에 걸치면 세대수를 나눠 준다
        share = units / max(len(dongs), 1)
        for dc in dongs:
            b = dong_acc[dc]
            b["n"] += 1
            b["w"] += w * share
            b["units"] += share
            b["stages"][label] += 1

    # ── 1) 국토부 전국 (기준) ──
    path = os.path.join(DATA, "국토교통부_전국 도시정비사업 통합 데이터_20260518.csv")
    n_base = 0
    for r in csv.DictReader(open(path, encoding="cp949")):
        code = M.sigungu(r["시도"], r["시군구"])
        if not code:
            continue
        w, label = stage_of(r["현 사업추진단계"])
        try:
            units = int(re.sub(r"[^\d]", "", r.get("공급 예정 세대수") or "0") or 0)
        except ValueError:
            units = 0
        if not units:
            units = guess_units(r.get("사업유형"))
        add(code, w, label, units)
        n_base += 1
    print(f"국토부 전국      {n_base}건")

    # ── 2) 서울 (법정동명 있음 → 동 단위) ──
    path = os.path.join(DATA, "서울특별시_서울시 정비사업 데이터_20211227.csv")
    n_seoul = 0
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="cp949")):
            code = M.sigungu("서울특별시", r["시군구명"])
            if not code:
                continue
            w, label = stage_of(r["시행단계"])
            dongs = M.dong_in(code, r.get("법정동명"))
            if w > 0 and dongs:
                # 서울 파일에는 세대수가 없다. 구역 면적으로 규모를 잰다.
                # 정비구역 1㎡당 약 0.017세대(≈용적률 200%, 전용 85㎡ 기준)로 환산.
                try:
                    area = float(re.sub(r"[^\d.]", "", r.get("정비구역 면적(제곱미터)") or "0") or 0)
                except ValueError:
                    area = 0
                units = int(area * 0.017) if area > 0 else guess_units(r.get("정비유형"))
                share = units / len(dongs)
                for dc in dongs:
                    b = dong_acc[dc]
                    b["n"] += 1
                    b["w"] += w * share
                    b["units"] += share
                    b["stages"][label] += 1
                n_seoul += 1
    print(f"서울 (동 단위)   {n_seoul}건")

    # ── 3) 경기 (주소에서 동 추출) ──
    path = os.path.join(DATA, "경기도_일반정비사업추진현황.csv")
    n_gg = 0
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="cp949")):
            w, label = stage_of(r["사업단계"])
            _, _, dong = parse_addr(r.get("위치"))
            code = M.sigungu("경기도", r["시군명"])
            if not code:
                continue
            if w > 0 and dong:
                dongs = M.dong_in(code, dong)
                try:
                    units = int(float(r.get("사업시행세대수총계") or 0))
                except ValueError:
                    units = 0
                if not units:
                    units = guess_units(r.get("사업유형"))
                share = units / max(len(dongs), 1)
                for dc in dongs:
                    b = dong_acc[dc]
                    b["n"] += 1
                    b["w"] += w * share
                    b["units"] += share
                    b["stages"][label] += 1
                if dongs:
                    n_gg += 1
            elif w <= 0:
                seen_done += 1
    print(f"경기 (동 단위)   {n_gg}건")

    # ── 4) 인천 ──
    path = os.path.join(DATA, "인천광역시_도시 및 주거환경 정비사업 추진현황_20260531.csv")
    n_ic = 0
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="cp949")):
            code = M.sigungu("인천광역시", (r.get("구명") or "").strip())
            if not code:
                continue
            w, label = stage_of(r.get("진행단계"))
            _, _, dong = parse_addr(r.get("위치"))
            if w > 0 and dong:
                dongs = M.dong_in(code, dong)
                units = guess_units(r.get("사업유형"))
                share = units / max(len(dongs), 1)
                for dc in dongs:
                    b = dong_acc[dc]
                    b["n"] += 1
                    b["w"] += w * share
                    b["units"] += share
                    b["stages"][label] += 1
                if dongs:
                    n_ic += 1
    print(f"인천 (동 단위)   {n_ic}건")
    print(f"완료·해제 제외    {seen_done}건")

    # ── 점수화 ──
    # 세대수 가중합의 제곱근을 쓴다. 1만 세대와 4만 세대의 체감 차이가
    # 4배는 아니기 때문. 전국 백분위로 30~96에 매핑한다.
    def score(acc):
        if not acc:
            return {}
        codes = list(acc)
        raw = np.sqrt(np.array([acc[c]["w"] for c in codes]))
        order = raw.argsort()
        ranks = np.empty(len(raw))
        ranks[order] = np.arange(len(raw))
        pct = ranks / max(len(raw) - 1, 1)
        return {c: int(round(30 + p * 66)) for c, p in zip(codes, pct)}

    sgg_score = score(sgg_acc)
    dong_score = score(dong_acc)

    rows = {}
    for c, a in sgg_acc.items():
        top = max(a["stages"].items(), key=lambda kv: kv[1])[0] if a["stages"] else None
        rows[c] = {"redev": sgg_score[c], "n": a["n"],
                   "units": int(a["units"]), "top": top}
    for c, a in dong_acc.items():
        top = max(a["stages"].items(), key=lambda kv: kv[1])[0] if a["stages"] else None
        rows[c] = {"redev": dong_score[c], "n": a["n"],
                   "units": int(a["units"]), "top": top}

    out = os.path.join(ROOT, "data-redev.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("// 자동 생성 — 직접 고치지 마세요.\n")
        f.write("// 생성: python3 tools/build-redev.py\n")
        f.write("// 출처: 국토교통부·서울시·경기도·인천시 정비사업 공개데이터 (data/)\n")
        f.write("//\n")
        f.write("// redev  정비사업 규모 — (공급세대수 x 단계가중치) 합의 전국 백분위\n")
        f.write("// n      진행 중인 정비구역 수 (준공·청산 제외)\n")
        f.write("// units  공급 예정 세대수\n")
        f.write("// top    가장 많은 단계\n")
        f.write("const REDEV = ")
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print(f"\n{out}")
    print(f"  시군구 {len(sgg_acc)}곳 · 행정동 {len(dong_acc)}곳")


if __name__ == "__main__":
    main()
