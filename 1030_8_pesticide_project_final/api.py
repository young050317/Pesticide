from pathlib import Path
import math
import re
from collections import defaultdict
from functools import lru_cache

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
USAGE_FILE = BASE_DIR / "pesticide_usage_original.xlsx"
DB_FILE = BASE_DIR / "pesticide_database.xlsx"

app = FastAPI(title="농약 정보 DSS")

@app.middleware("http")
async def no_cache_for_app(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/api/") or request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

print("농약 데이터를 불러오는 중입니다...")

# -----------------------------------------------------------------------------
# 1. 공개 농약 사용정보(작물/병해충/사용방법 등)
# -----------------------------------------------------------------------------
# 원본 Excel은 14만 행 이상이라 매번 openpyxl로 읽으면 서버 시작이 느려집니다.
# 프로젝트에 함께 넣은 usage_data.csv를 우선 사용하고, CSV가 없을 때만 원본 Excel을 읽습니다.
USAGE_CSV = BASE_DIR / "usage_data.csv"
if USAGE_CSV.exists():
    usage_df = pd.read_csv(USAGE_CSV, dtype=str, encoding="utf-8-sig").fillna("")
else:
    raw_usage = pd.read_excel(USAGE_FILE, header=None, dtype=str).fillna("")
    usage_header_row = None
    for i in range(min(10, len(raw_usage))):
        values = raw_usage.iloc[i].astype(str).str.strip().tolist()
        if "등록번호" in values:
            usage_header_row = i
            break
    if usage_header_row is None:
        raise ValueError("농약제품 목록에서 '등록번호' 헤더를 찾지 못했습니다.")
    usage_df = raw_usage.iloc[usage_header_row + 1:].copy()
    usage_df.columns = raw_usage.iloc[usage_header_row].astype(str).str.strip()
    usage_df = usage_df.reset_index(drop=True).fillna("")
    usage_df.columns = [str(c).strip() for c in usage_df.columns]

# -----------------------------------------------------------------------------
# 2. 정형화한 최종 DB
# -----------------------------------------------------------------------------
products_df = pd.read_excel(DB_FILE, sheet_name="제품", dtype=str).fillna("")
product_active_df = pd.read_excel(DB_FILE, sheet_name="제품_원제", dtype=str).fillna("")
active_df = pd.read_excel(DB_FILE, sheet_name="원제", dtype=str).fillna("")
active_h_df = pd.read_excel(DB_FILE, sheet_name="원제_유해위험문구", dtype=str).fillna("")
active_class_df = pd.read_excel(DB_FILE, sheet_name="원제_별표5구분", dtype=str).fillna("")
active_mech_df = pd.read_excel(DB_FILE, sheet_name="원제_작용기작", dtype=str).fillna("")
mechanism_df = pd.read_excel(DB_FILE, sheet_name="작용기작", dtype=str).fillna("")

products_df["제품ID"] = products_df["제품ID"].astype(str).str.strip()
product_active_df["제품ID"] = product_active_df["제품ID"].astype(str).str.strip()
product_active_df["원제ID"] = product_active_df["원제ID"].astype(str).str.strip()
active_df["원제ID"] = active_df["원제ID"].astype(str).str.strip()
active_h_df["원제ID"] = active_h_df["원제ID"].astype(str).str.strip()
active_class_df["원제ID"] = active_class_df["원제ID"].astype(str).str.strip()
active_mech_df["원제ID"] = active_mech_df["원제ID"].astype(str).str.strip()
mechanism_df["작용기작ID"] = mechanism_df["작용기작ID"].astype(str).str.replace(".0", "", regex=False).str.strip()

# 빠른 조회용 사전
product_by_reg = {
    str(row["등록번호"]): row.to_dict()
    for _, row in products_df.iterrows()
}
product_id_by_reg = {
    str(row["등록번호"]): str(row["제품ID"])
    for _, row in products_df.iterrows()
}
reg_by_product_id = {v: k for k, v in product_id_by_reg.items()}

usage_by_reg = defaultdict(list)
for record in usage_df.to_dict("records"):
    usage_by_reg[str(record.get("등록번호", ""))].append(record)

active_by_id = {
    str(row["원제ID"]): row.to_dict()
    for _, row in active_df.iterrows()
}

hcodes_by_active = defaultdict(list)
for _, row in active_h_df.iterrows():
    code = str(row.get("H코드", "")).strip()
    if code:
        hcodes_by_active[str(row["원제ID"])].append(code)

mechanisms_by_active = defaultdict(list)
for _, row in active_mech_df.iterrows():
    aid = str(row.get("원제ID", "")).strip()
    mid = str(row.get("작용기작ID", "")).strip()
    if mid.endswith(".0"):
        mid = mid[:-2]
    detail = mechanism_df[mechanism_df["작용기작ID"] == mid]
    if not detail.empty:
        d = detail.iloc[0].to_dict()
        mechanisms_by_active[aid].append({
            "약종": row.get("약종", ""),
            "기호": row.get("기호", ""),
            "작용기작그룹": d.get("작용기작그룹", ""),
            "세부": d.get("세부", ""),
        })
    else:
        mechanisms_by_active[aid].append({
            "약종": row.get("약종", ""),
            "기호": row.get("기호", ""),
            "작용기작그룹": "",
            "세부": "",
        })

# 제품-원제 연결은 순번을 기준으로 유지한다.
product_active_df["_순번"] = pd.to_numeric(product_active_df.get("순번", ""), errors="coerce")
product_active_df = product_active_df.sort_values(["제품ID", "_순번"], na_position="last")

active_ids_by_product = defaultdict(list)
active_content_by_product = defaultdict(list)

for _, row in product_active_df.iterrows():
    pid = str(row.get("제품ID", "")).strip()
    aid = str(row.get("원제ID", "")).strip()
    if aid in active_by_id and aid not in active_ids_by_product[pid]:
        active_ids_by_product[pid].append(aid)

    # 새 전처리 DB에 '원제함량(%)'이 있으면 제품_원제 연결 순서 그대로 사용한다.
    if "원제함량(%)" in product_active_df.columns:
        content = str(row.get("원제함량(%)", "") or "").strip().replace(",", "")
        active_content_by_product[pid].append(content)

product_by_id = {str(row["제품ID"]): row.to_dict() for _, row in products_df.iterrows()}
product_count_by_active = defaultdict(int)
for pid, aids in active_ids_by_product.items():
    for aid in aids:
        product_count_by_active[aid] += 1

product_search_values = {}
for pid, product in product_by_id.items():
    values = [(product.get("상표명", ""), 1.0), (product.get("품목명", ""), 1.0), (product.get("등록번호", ""), 0.9)]
    for aid in active_ids_by_product.get(pid, []):
        active = active_by_id.get(aid, {})
        values.append((active.get("한글명", ""), 1.0))
        values.append((active.get("영문명", ""), 1.0))
        values.append((active.get("CAS", ""), 0.8))
    product_search_values[pid] = values

def normalize(value):
    value = "" if value is None else str(value)
    value = value.strip().lower()
    return re.sub(r"\s+", " ", value)


def clean_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def match_rank(text, keyword):
    text = normalize(text)
    keyword = normalize(keyword)
    if not text or not keyword:
        return 99
    if text == keyword:
        return 0
    if text.startswith(keyword):
        return 1
    if keyword in text:
        return 2
    return 99


def split_keywords(value):
    return [x for x in re.split(r"\s+", normalize(value)) if x]


def best_rank(values, keywords):
    ranks = []
    for value in values:
        for keyword in keywords:
            r = match_rank(value, keyword)
            if r < 99:
                ranks.append(r)
    return min(ranks) if ranks else 99


def parse_safety_days(value):
    text = normalize(value)
    m = re.search(r"(\d+)\s*일", text)
    return int(m.group(1)) if m else None


# -----------------------------------------------------------------------------
# 3. 독성(인체건강) 지표: 덴마크 PLI Human Health Table 3.2 점수 매핑
# -----------------------------------------------------------------------------
# 설계서 Table 3.2의 점수 매핑을 사용한다. 공식 덴마크 제품 PLI가 아니라,
# 국내 공개 원제 H-code에 표 점수를 대응시킨 '자체 참고지표'다.
HUMAN_H_POINTS = {
    "H302": 10, "H315": 10, "H304": 10, "H335": 10, "EUH066": 10,
    "H332": 15, "H312": 15, "H319": 15,
    "H317": 20,
    "H336": 30,
    "H301": 50, "H334": 50, "H362": 50,
    "H331": 70, "H311": 70, "H351": 70, "H361": 70, "H371": 70, "H373": 70, "H341": 70,
    "H318": 70,                      # 설계서 Table 3.2 (70점 구간)
    "H370": 100, "H350": 100, "H340": 100, "H372": 100,
}

# 세부 Category에 따라 점수가 달라지는 코드: 확정 불가 시 표에서 가능한 (최소, 최대) 범위
CATEGORY_RANGE = {
    "H300": (70, 85),    # Acute Tox. 2 → 70, 1 → 85
    "H310": (70, 85),
    "H330": (85, 100),   # Acute Tox. 2 → 85, 1 → 100
    "H314": (70, 100),   # Skin Corr. 1B → 70, 1A → 100
}

SEVERE_CODES = {"H350", "H340", "H360", "H370", "H372"}

# 독성 강도 등급 기준 (원제 참고점수 기준) - 필요하면 여기만 바꾸면 화면 전체에 반영됩니다.
LEVEL_LOW_MAX = 50      # 0~49점  : 낮음
LEVEL_HIGH_MIN = 100    # 100점~  : 높음 (그 사이는 중간)
LEVEL_LABEL = {"low": "유해성 낮음", "mid": "유해성 중간", "high": "유해성 높음", "none": "자료 없음"}

H_TEXT = {}
_h_sheet = pd.read_excel(DB_FILE, sheet_name="별표5_H문구", dtype=str).fillna("")
for _, _r in _h_sheet.iterrows():
    H_TEXT[str(_r["H코드"]).strip()] = re.sub(r"\s*\(.*?\)\s*$", "", str(_r["문구"]).strip())
H_TEXT.setdefault("EUH066", "반복 노출 시 피부를 건조하거나 갈라지게 할 수 있음")

GHS_INFO = {
    "GHS02": ("인화성", "불꽃"),
    "GHS04": ("고압가스", "가스 실린더"),
    "GHS05": ("부식성", "금속·피부 부식"),
    "GHS06": ("급성 독성", "해골과 X자형 뼈"),
    "GHS07": ("유해·자극성", "감탄부호"),
    "GHS08": ("건강 유해성", "흉부 손상 형상"),
    "GHS09": ("수생환경 유해성", "죽은 나무와 물고기"),
}
GHS_H_MAP = {
    "GHS02": {"H220", "H221", "H222", "H223", "H224", "H225", "H226", "H228", "H241", "H242"},
    "GHS04": {"H280", "H281", "H282", "H283"},
    "GHS05": {"H290", "H314", "H318"},
    "GHS06": {"H300", "H301", "H310", "H311", "H330", "H331"},
    "GHS07": {"H302", "H312", "H332", "H315", "H317", "H319", "H335", "H336"},
    "GHS08": {"H304", "H334", "H340", "H341", "H350", "H351", "H360", "H361", "H362", "H370", "H371", "H372", "H373"},
    "GHS09": {"H400", "H410", "H411", "H412", "H413"},
}

_ghs_sheet = pd.read_excel(DB_FILE, sheet_name="원제_그림문자", dtype=str).fillna("")
ghs_by_active = defaultdict(list)
for _, _r in _ghs_sheet.iterrows():
    code = str(_r["GHS코드"]).strip()
    aid = str(_r["원제ID"]).strip()
    if code in GHS_INFO and code not in ghs_by_active[aid]:
        ghs_by_active[aid].append(code)

# H-code별 원제-별표5 구분 후보
classifications_by_active_code = defaultdict(list)
for _, row in active_class_df.iterrows():
    aid = str(row.get("원제ID", "")).strip()
    code = str(row.get("H코드", "")).strip()
    if aid and code:
        classifications_by_active_code[(aid, code)].append({
            "매핑상태": str(row.get("매핑상태", "")).strip(),
            "후보구분": str(row.get("후보구분", "")).strip(),
        })


def h_area(code):
    if code.startswith("EUH") or (code.startswith("H3") and not code.startswith("H39")):
        return "인체건강"
    if code.startswith("H4"):
        return "환경"
    if code.startswith("H2"):
        return "물리적 위험"
    return "기타"


def point_for_code(code, aid):
    """(확정점수 또는 None, (최소,최대) 또는 None). 점수 대상이 아니면 (None, None)."""
    if code in CATEGORY_RANGE:
        cands = classifications_by_active_code.get((aid, code), [])
        texts = " | ".join(x["후보구분"] for x in cands)
        statuses = {x["매핑상태"] for x in cands}
        if code == "H314" and "구분 1A" in texts and "1B" not in texts and "후보 여러 개" not in statuses:
            return 100, None
        if code == "H314" and "구분 1B" in texts and "1A" not in texts and "후보 여러 개" not in statuses:
            return 70, None
        if code in {"H300", "H310", "H330"} and "후보 여러 개" not in statuses and cands:
            has1, has2 = "구분 1" in texts, "구분 2" in texts
            lo, hi = CATEGORY_RANGE[code]
            if has1 and not has2:
                return hi, None
            if has2 and not has1:
                return lo, None
        # 세부 구분이 아직 확정되지 않은 경우에는 범위의 높은 값을
        # 임시 참고점수로 사용한다. 실제 법정 독성점수가 아닌 DSS 참고지표다.
        return CATEGORY_RANGE[code][1], None
    if code in HUMAN_H_POINTS:
        return HUMAN_H_POINTS[code], None
    return None, None


def level_of(score_upper, severe):
    if severe:
        return "high"
    if score_upper is None:
        return "none"
    if score_upper >= LEVEL_HIGH_MIN:
        return "high"
    if score_upper >= LEVEL_LOW_MAX:
        return "mid"
    return "low"


def code_level(point):
    if point is None:
        return "none"
    if point >= 70:
        return "high"
    if point >= 30:
        return "mid"
    return "low"


def pictograms_for(aid, codes):
    found = list(ghs_by_active.get(aid, []))
    if not found:
        cs = set(codes)
        found = [g for g, hs in GHS_H_MAP.items() if cs & hs]
    return [{"code": g, "name": GHS_INFO[g][0], "description": GHS_INFO[g][1]} for g in sorted(found)]


@lru_cache(maxsize=None)
def ingredient_tox_cached(aid):
    aid = str(aid)
    active = active_by_id.get(aid, {})
    codes = list(dict.fromkeys(hcodes_by_active.get(aid, [])))

    rows, total, lo_extra, hi_extra, unknown = [], 0, 0, 0, []
    individual = []
    for code in codes:
        area = h_area(code)
        point, rng = point_for_code(code, aid)
        row = {"H코드": code, "문구": H_TEXT.get(code, ""), "영역": area,
               "점수": point, "점수범위": list(rng) if rng else None,
               "등급": code_level(point if point is not None else (rng[1] if rng else None))}
        if point is not None:
            total += point
            individual.append(point)
            row["상태"] = "확인"
        elif rng:
            lo_extra += rng[0]
            hi_extra += rng[1]
            unknown.append(code)
            row["상태"] = "범위"
        else:
            if area == "인체건강":
                # 설계서 Table 3.2에 점수가 없는 인체건강 코드(예: H360): 0점이 아니라 '점수 미정'
                row["상태"] = "점수 미정"
                row["등급"] = "high" if code in SEVERE_CODES else "none"
            else:
                row["상태"] = "점수 제외"
                row["등급"] = "none"
        rows.append(row)

    severe = sorted(set(codes) & SEVERE_CODES)
    if not codes:
        state = "nodata"
    elif unknown:
        state = "range"
    else:
        state = "confirmed"

    score = total if state == "confirmed" else None
    score_range = [total + lo_extra, total + hi_extra] if state == "range" else None
    upper = score if score is not None else (score_range[1] if score_range else None)
    level = level_of(upper, severe) if state != "nodata" else "none"
    note = ""
    if state == "nodata":
        status = str(active.get("표시사항상태", ""))
        note = status if status else "H-code 자료 없음"
    elif state == "confirmed" and total == 0:
        note = "인체건강 H-code 없음 (환경·물리적 위험 코드만 확인)" if codes else ""

    return {
        "원제ID": aid,
        "한글명": active.get("한글명", ""),
        "영문명": active.get("영문명", ""),
        "CAS": active.get("CAS", ""),
        "원제유형": active.get("원제유형", ""),
        "표시사항상태": active.get("표시사항상태", ""),
        "신호어": active.get("신호어", ""),
        "신호어확정여부": active.get("신호어확정여부", ""),
        "H코드": codes,
        "상태": state,
        "점수": score,
        "점수범위": score_range,
        "점수상한": upper,
        "정규화참고값": None if score is None else round(score / 300, 3),
        "최고개별점수": max(individual) if individual else None,
        "등급": level,
        "등급명": LEVEL_LABEL[level],
        "중대한유해성": severe,
        "미확정코드": unknown,
        "H코드상세": rows,
        "그림문자": pictograms_for(aid, codes),
        "비고": note,
    }


def ingredient_tox(aid):
    data = dict(ingredient_tox_cached(str(aid)))
    data["작용기작"] = mechanisms_by_active.get(str(aid), [])
    return data


LIVESTOCK_RANK = {"Ⅳ": 0, "Ⅲ": 1, "Ⅱ": 2, "Ⅰ": 3}


def livestock_info(text):
    text = str(text or "")
    grade = next((g for g in ["Ⅳ", "Ⅲ", "Ⅱ", "Ⅰ"] if text.startswith(g)), "")
    level = {"Ⅳ": "low", "Ⅲ": "mid", "Ⅱ": "high", "Ⅰ": "high"}.get(grade, "none")
    return {"표기": text or "미기재", "등급": level, "순위": LIVESTOCK_RANK.get(grade, 9)}



def _parse_percent(value):
    raw = str(value or "").strip().replace(",", "")
    raw = raw.replace("%", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return None
    return float(raw)


def parse_active_contents(product, active_count):
    """제품_원제의 원제함량(%)을 연결 순서대로 우선 사용한다.

    새 전처리 DB에는 제품_원제에 원제별 함량이 들어오므로 복합제도
    일반명/주성분함량을 다시 추정하지 않고 연결 순서 그대로 매칭한다.
    새 컬럼이 없거나 값이 부족한 경우에만 기존 주성분함량을 보조적으로 사용한다.
    """
    if active_count <= 0:
        return None

    pid = str(product.get("제품ID", "")).strip()
    mapped = active_content_by_product.get(pid, [])
    if mapped and len(mapped) >= active_count:
        values = [_parse_percent(x) for x in mapped[:active_count]]
        if all(v is not None and 0 <= v <= 100 for v in values):
            return values

    # 구 DB 호환용: 제품의 주성분함량을 제품_원제 연결 순서에 맞춰 분해
    raw = str(product.get("주성분함량", "") or "").strip().replace(",", "")
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:\(([^()]*)\))?\s*%\s*", raw)
    if not m:
        return None

    total = float(m.group(1))
    parts = m.group(2)

    if active_count == 1:
        return [total]

    if not parts:
        return None

    values = [_parse_percent(x) for x in parts.split("+")]
    if len(values) != active_count or any(v is None for v in values):
        return None
    if abs(sum(values) - total) > 0.011:
        return None
    return values


def weighted_product_score(product, ingredient_data):
    """원제별 H-code 참고점수에 제품 내 원제 함량을 반영한 제품 참고점수.
    성분별 기여점수 = 원제 참고점수 × 함량(%)/100.
    이는 공식 독성점수나 실제 위해성 평가가 아닌 DSS 참고지표다.
    """
    contents = parse_active_contents(product, len(ingredient_data))
    if not contents or len(contents) != len(ingredient_data):
        return {"상태": "미확인", "점수": None, "점수범위": None, "기여": []}

    contributions = []
    low_total = 0.0
    high_total = 0.0
    has_range = False

    for ing, pct in zip(ingredient_data, contents):
        low = ing.get("점수")
        high = ing.get("점수")
        if ing.get("점수범위"):
            low, high = ing["점수범위"]
            has_range = True
        if low is None or high is None:
            return {"상태": "미확인", "점수": None, "점수범위": None, "기여": []}
        low_c = low * pct / 100
        high_c = high * pct / 100
        low_total += low_c
        high_total += high_c
        contributions.append({
            "원제ID": ing.get("원제ID", ""),
            "원제명": ing.get("한글명") or ing.get("영문명") or "",
            "함량": pct,
            "원제점수": ing.get("점수"),
            "원제점수범위": ing.get("점수범위"),
            "기여점수": high_c if low_c == high_c else None,
            "기여점수범위": [low_c, high_c] if low_c != high_c else None,
        })

    return {
        "상태": "범위" if has_range else "확정",
        "점수": low_total if not has_range else None,
        "점수범위": [low_total, high_total] if has_range else None,
        "기여": contributions,
    }


@lru_cache(maxsize=None)
def product_tox_cached(pid):
    pid = str(pid)
    product = product_by_id.get(pid, {})
    aids = active_ids_by_product.get(pid, [])
    ings = [ingredient_tox_cached(a) for a in aids]
    n = len(ings)

    states = [i["상태"] for i in ings]
    severe = sorted({c for i in ings for c in i["중대한유해성"]})
    weighted = weighted_product_score(product, ings)

    if n == 0 or all(s == "nodata" for s in states):
        state = "nodata"
    elif weighted["상태"] == "미확인":
        state = "partial" if any(s == "nodata" for s in states) else "range" if any(s == "range" for s in states) else "incomplete"
    elif weighted["상태"] == "범위":
        state = "range"
    else:
        state = "confirmed"

    ref = weighted["점수"]
    ref_range = weighted["점수범위"]
    upper = ref if ref is not None else (ref_range[1] if ref_range else None)
    level = level_of(upper, severe) if (upper is not None or severe) else "none"

    ghs = {}
    for i in ings:
        for g in i["그림문자"]:
            ghs[g["code"]] = g
    livestock = livestock_info(product.get("인축독성", ""))

    # 제품의 공식 인축독성과 DSS 참고점수는 서로 다른 지표다.
    # 정렬은 품목 인축독성 → 중대 H-code → 제품 참고점수 순으로 이어진다.
    incomplete = 0 if state in ("confirmed", "range") else 1

    return {
        "상태": state,
        "추천가능": state == "confirmed" and n > 0,
        "참고점수": ref,
        "참고점수범위": ref_range,
        "참고점수_확정": state == "confirmed",
        "제품독성점수": ref,
        "제품독성점수범위": ref_range,
        "점수합": sum((x["점수"] or 0) for x in ings),
        "등급": level,
        "등급명": LEVEL_LABEL[level],
        "중대한유해성": severe,
        "원제수": n,
        "원제": ings,
        "성분별기여점수": weighted["기여"],
        "그림문자": [ghs[k] for k in sorted(ghs)],
        "인축독성": livestock,
        "어독성": str(product.get("어독성", "") or "미기재"),
        "정렬키": (LEVEL_ORDER[level], incomplete, ref if ref is not None else float("inf"),
                   len(severe), sum((x["점수"] or 0) for x in ings),
                   livestock["순위"], n),
    }


def product_tox(product):
    return product_tox_cached(str(product.get("제품ID", "")))


def tox_brief(t):
    return {
        "등급": t["등급"], "등급명": t["등급명"], "점수": t["참고점수"], "상태": t["상태"],
        "점수확정": t["참고점수_확정"],
        "그림문자": [g["code"] for g in t["그림문자"]],
        "중대한유해성": t["중대한유해성"],
        "인축독성": t["인축독성"],
    }


# 사전식 정렬: 공식 제품 급성독성 → 중대한 원제 H코드 → 원제 구간 → 희석 명목농도 → 원제 참고점수.
# 각 항목은 합산하지 않으며, 자료 없음은 0 또는 안전으로 해석하지 않는다.
FIXED_NOTICE = "원제·품목의 유해성 정보이며 위해성 판정이 아닙니다. 사용 전 제품 라벨과 공식 등록사항을 확인하세요."
SEVERE_RANK_CODES = {"H340", "H350", "H360", "H370", "H372"}
LEVEL_ORDER = {"low": 0, "mid": 1, "high": 2, "none": 3}


def biological_type(product):
    """제품에 연결된 원제의 '원제유형'을 기준으로 비화학 제품을 판별한다.
    제품명/품목명 문자열을 이용해 임의로 생물농약을 판별하지 않는다.
    """
    pid = str(product.get("제품ID", "")).strip()
    types = []
    for aid in active_ids_by_product.get(pid, []):
        raw = str(active_by_id.get(aid, {}).get("원제유형", "") or "").strip()
        if raw and raw not in types:
            types.append(raw)

    if not types:
        return "미확인"

    # 새 DB의 표준 유형과 기존 DB의 세부 표기를 함께 처리한다.
    normalized = []
    for raw in types:
        if raw.startswith("미생물"):
            normalized.append("미생물")
        elif raw.startswith("생화학"):
            normalized.append("생화학")
        elif "페로몬" in raw:
            normalized.append("기타")
        elif raw == "기타":
            normalized.append("기타")
        elif raw == "화학":
            normalized.append("화학")
        else:
            normalized.append(raw)

    if "미생물" in normalized:
        return "미생물농약"
    if "생화학" in normalized:
        return "생화학농약"
    if "기타" in normalized:
        return "기타"
    if all(x == "화학" for x in normalized):
        return "화학농약"
    return "미확인"


def biological(product):
    return biological_type(product) in {"미생물농약", "생화학농약", "기타"}


def concentration_info(product, usage):
    """희석 후 명목상 유효성분 농도(%).

    희석배수가 공란인 사용등록은 원액 사용으로 보고 희석배수 1을 적용한다.
    원액 제품의 사용량은 추천 비교값으로 사용하지 않는다.
    """
    raw = str(product.get("주성분함량", "")).strip().replace(",", "")
    dilution_raw = str((usage or {}).get("희석배수", "") or "").strip().replace(",", "")

    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:\(([^()]*)\))?\s*%\s*", raw)
    if not m:
        return {"값": None, "상태": "주성분 함량 미확인", "원액": False}

    total = float(m.group(1))
    if not 0 <= total <= 100:
        return {"값": None, "상태": "주성분 함량 확인 필요", "원액": False}

    # 희석배수 공란 = 원액
    if not dilution_raw:
        return {
            "값": round(total, 8),
            "상태": "원액 사용",
            "원액": True,
            "희석배수": 1,
        }

    d = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*배?\s*", dilution_raw)
    if not d:
        return {"값": None, "상태": "희석배수 형식 미확인", "원액": False}

    factor = float(d.group(1))
    if factor <= 0:
        return {"값": None, "상태": "희석배수 확인 필요", "원액": False}

    return {
        "값": round(total / factor, 8),
        "상태": "희석 후 명목상 유효성분 총농도(%) · 실제 노출량 아님",
        "원액": False,
        "희석배수": factor,
    }


def safety_interval(usage):
    """수확 N일 전 표기만 비교. 발생초기/파종기 등은 비교 불가."""
    raw = str((usage or {}).get("안전사용시기", "")).strip().replace(" ", "")
    m = re.fullmatch(r"수확(\d+)일전", raw)
    return int(m.group(1)) if m else None


def decision_info(product, usage=None):
    usage = usage or {}
    t = product_tox(product)
    bio = biological(product)
    severe = sorted(set(t["중대한유해성"]) & SEVERE_RANK_CODES)
    concentration = concentration_info(product, usage) if not bio else {"값": None, "상태": "생물농약 별도 기준"}
    source_unknown = t["상태"] != "confirmed"
    if source_unknown:
        severe_position = "중대 유해성 확인 제품보다 후순위"
        severe_reason = "원제 H-code 자료가 완전히 확인되지 않아 '중대 유해성 없음'으로 볼 수 없기 때문입니다."
    elif severe:
        severe_position = "중대 유해성 확인"
        severe_reason = "중대 유해성 H-code가 확인되어 중대 유해성 없음 제품보다 후순위입니다."
    else:
        severe_position = "중대 유해성 없음"
        severe_reason = ""

    missing = (source_unknown or t["인축독성"]["순위"] == 9
               or (not bio and concentration["값"] is None))
    return {"인축독성": t["인축독성"], "중대코드": severe,
            "원제구간": t["등급명"] if t["등급"] != "none" else "미확인",
            "원제자료미확인": source_unknown, "희석농도": concentration,
            "생물농약": bio, "생물농약유형": biological_type(product), "자료미확인": missing,
            "중대유해성순위": severe_position, "중대유해성순위이유": severe_reason,
            "안전사용시기": (usage or {}).get("안전사용시기", ""),
            "안전사용일수": safety_interval(usage),
            "원제참고점수": t["참고점수"]}


def decision_key(product, usage=None):
    d = decision_info(product, usage)
    t = product_tox(product)
    c = d["희석농도"]["값"]
    # 중대 유해성 확인 여부의 순서:
    # 0 = 중대 유해성 없음(원제자료 확인)
    # 1 = 중대 유해성 확인
    # 2 = 원제자료 미확인
    # 미확인을 '없음'으로 볼 수 없으므로, 중대 유해성 확인 제품보다도 후순위로 둔다.
    severe_rank = 2 if d["원제자료미확인"] else (1 if d["중대코드"] else 0)
    return (t["인축독성"]["순위"], severe_rank, LEVEL_ORDER[t["등급"]],
            1 if c is None else 0, c if c is not None else float("inf"),
            1 if t["참고점수"] is None else 0,
            t["참고점수"] if t["참고점수"] is not None else float("inf"),
            1 if safety_interval(usage) is None else 0,
            safety_interval(usage) if safety_interval(usage) is not None else float("inf"),
            str(product.get("상표명", "")), str(product.get("등록번호", "")))


def choice_reason(first, second):
    if second is None:
        return "등록된 제품이 1개이므로 상대 순위를 매기지 않고 정보만 제공합니다."

    labels = [
        "품목 인축독성 등급",
        "중대 유해성 확인 여부",
        "원제 유해성 구간",
        "희석 후 명목상 농도",
        "원제 참고점수",
        "안전사용시기",
        "제품명(동점 정렬)",
    ]
    ak, bk = decision_key(*first), decision_key(*second)
    idx = next((i for i in range(len(ak)) if ak[i] != bk[i]), None)

    if idx == 1:
        first_info = decision_info(*first)
        second_info = decision_info(*second)
        if first_info["원제자료미확인"] and not second_info["원제자료미확인"]:
            return (
                f"{first[0].get('상표명', '')}은 원제 자료가 미확인되어 "
                f"중대 유해성 확인 제품보다 후순위로 배치했습니다. "
                f"미확인은 안전을 의미하지 않습니다."
            )
        if second_info["원제자료미확인"] and not first_info["원제자료미확인"]:
            return (
                f"{first[0].get('상표명', '')}은 원제 H-code 자료가 확인된 제품으로, "
                f"자료 미확인 제품보다 우선 표시했습니다."
            )

    group = (
        0 if idx == 0 else
        1 if idx == 1 else
        2 if idx == 2 else
        3 if idx in (3, 4) else
        4 if idx in (5, 6) else
        5 if idx in (7, 8) else 6
    )
    return f"{labels[group]} 기준으로 {first[0].get('상표명', '')} 제품을 우선 표시했습니다."


def pick_key(t, relevance, safety_days):
    # 기존 외부 호출 호환용. 새 추천/비교/대안은 decision_key 사용.
    return (t["인축독성"]["순위"], len(t["중대한유해성"]) > 0,
            LEVEL_ORDER[t["등급"]], t["참고점수"] if t["참고점수"] is not None else 9999)


# -----------------------------------------------------------------------------
# 4. 검색 공통
# -----------------------------------------------------------------------------
def product_search_score(product, product_keywords):
    if not product_keywords:
        return 0
    values = product_search_values.get(str(product.get("제품ID", "")), [])
    total = 0
    for keyword in product_keywords:
        best = -1
        for value, weight in values:
            r = match_rank(value, keyword)
            score = {0: 100, 1: 70, 2: 40}.get(r, -1) * (weight if r < 99 else 1)
            best = max(best, score)
        if best < 0:
            return -1
        total += best
    return total


def usage_match_and_score(usages, crop_keywords, pest_keywords):
    """같은 사용등록 행에서 작물+병해충이 함께 맞는 행을 우선한다."""
    best_score, best_usage = -1, None
    for usage in usages:
        crop_score = pest_score = 0
        if crop_keywords:
            ranks = [match_rank(usage.get("작물명", ""), k) for k in crop_keywords]
            if any(r == 99 for r in ranks):
                continue
            crop_score = sum({0: 100, 1: 70, 2: 40}[r] for r in ranks)
        if pest_keywords:
            ranks = [match_rank(usage.get("적용병해충", ""), k) for k in pest_keywords]
            if any(r == 99 for r in ranks):
                continue
            pest_score = sum({0: 100, 1: 70, 2: 40}[r] for r in ranks)
        score = crop_score + pest_score
        if score > best_score:
            best_score, best_usage = score, usage
    return best_score, best_usage


USAGE_FIELDS = ["작물명", "적용병해충", "희석배수", "사용량", "사용적기", "사용방법", "안전사용시기", "안전사용횟수"]


def search_all(product="", crop="", pest=""):
    """조건에 맞는 모든 제품(등록번호 단위)을 관련도와 함께 돌려준다. 정렬/자르기는 호출한 쪽에서 한다."""
    product_keywords = split_keywords(product)
    crop_keywords = split_keywords(crop)
    pest_keywords = split_keywords(pest)
    results = []

    for reg, base in product_by_reg.items():
        pscore = product_search_score(base, product_keywords)
        if product_keywords and pscore < 0:
            continue
        usages = usage_by_reg.get(reg, [])
        uscore, best_usage = usage_match_and_score(usages, crop_keywords, pest_keywords)
        if (crop_keywords or pest_keywords) and uscore < 0:
            continue
        if not product_keywords:
            pscore = 0
        if not crop_keywords and not pest_keywords:
            uscore = 0
            best_usage = usages[0] if usages else {}
        item = dict(base)
        item["관련도"] = round(pscore + uscore, 1)
        for f in USAGE_FIELDS:
            item[f] = best_usage.get(f, "") if best_usage else ""
        results.append(item)
    return results


def attach_tox(items):
    for item in items:
        t = product_tox(item)
        brief = tox_brief(t)
        d = decision_info(item, item)
        brief["희석농도"] = d["희석농도"]
        brief["중대유해성순위"] = d["중대유해성순위"]
        brief["중대유해성순위이유"] = d["중대유해성순위이유"]
        item["독성"] = brief
    return items


# -----------------------------------------------------------------------------
# 기본 페이지 / 통계
# -----------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/api/stats")
def stats():
    return {
        "제품수": int(len(products_df)),
        "원제수": int(len(active_df)),
        "사용등록행수": int(len(usage_df)),
        "등록번호수": int(len(product_by_reg)),
    }


# -----------------------------------------------------------------------------
# 검색 (페이지네이션 + 정렬 + 용도 필터)
#   기본 정렬 : 품목 인축독성 우선 추천순
#   독성순    : 원제 유해성 구간 우선
#   이름순    : 상표명 가나다순
# -----------------------------------------------------------------------------
@app.get("/api/search")
def search_pesticides(
    product: str = Query(""),
    crop: str = Query(""),
    pest: str = Query(""),
    sort: str = Query("recommend"),
    purpose: str = Query(""),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    if not (split_keywords(product) or split_keywords(crop) or split_keywords(pest)):
        return {"total": 0, "results": [], "facets": {}, "message": "검색 조건을 하나 이상 입력해주세요."}

    # 구버전 화면/북마크가 relevance를 보내도 400 오류 없이 추천순으로 처리
    if sort in ("relevance", "", "related"):
        sort = "recommend"
    elif sort not in ("recommend", "tox", "name"):
        raise HTTPException(status_code=400, detail="정렬은 추천순, 독성 낮은순, 이름순만 지원합니다.")
    results = attach_tox(search_all(product, crop, pest))

    facets = defaultdict(int)
    for r in results:
        facets[r.get("용도", "") or "기타"] += 1

    if purpose:
        results = [r for r in results if (r.get("용도", "") or "기타") == purpose]

    def tox_key(r):
        return product_tox(r)["정렬키"]

    if sort == "recommend":
        # 품목 인축독성 → 중대 H코드 → 원제 구간 → 희석농도 → 참고점수 → 안전사용시기.
        # 생물농약은 별도 분류한다.
        results.sort(key=lambda r: (1 if biological(r) else 0,
                                    decision_key(r, r) if not biological(r) else (r.get("상표명", ""),)))
    elif sort == "tox":
        results.sort(key=lambda r: (tox_key(r), r.get("상표명", ""), r.get("등록번호", "")))
    elif sort == "name":
        results.sort(key=lambda r: (r.get("상표명", ""), r.get("등록번호", "")))

    total = len(results)
    page = results[offset: offset + limit]
    print(f"검색 product={product!r}, crop={crop!r}, pest={pest!r}, sort={sort}, purpose={purpose!r} -> {total}개 / {offset}~{offset + len(page)}")
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
        "sort": sort,
        "facets": dict(sorted(facets.items(), key=lambda x: -x[1])),
        "results": page,
    }


# -----------------------------------------------------------------------------
# 제품 상세
# -----------------------------------------------------------------------------
@app.get("/api/pesticides/{registration_number}")
def get_pesticide(registration_number: str):
    product = product_by_reg.get(registration_number)
    if product is None:
        raise HTTPException(status_code=404, detail="해당 농약 정보를 찾을 수 없습니다.")
    tox = product_tox(product)
    ingredients = []
    aids = active_ids_by_product.get(str(product.get("제품ID", "")), [])
    ingredient_contents = parse_active_contents(product, len(aids))
    for idx, aid in enumerate(aids):
        item = ingredient_tox(aid)
        if ingredient_contents and idx < len(ingredient_contents):
            item["함량"] = ingredient_contents[idx]
        else:
            item["함량"] = None
        item["성분기여점수"] = None
        item["성분기여점수범위"] = None
        if tox.get("성분별기여점수") and idx < len(tox["성분별기여점수"]):
            contribution = tox["성분별기여점수"][idx]
            item["성분기여점수"] = contribution.get("기여점수")
            item["성분기여점수범위"] = contribution.get("기여점수범위")
        ingredients.append(item)
    return {
        "product": product,
        "usages": usage_by_reg.get(registration_number, []),
        "ingredients": ingredients,
        "tox": {k: v for k, v in tox.items() if k not in ("원제", "정렬키")},
        "criteria": criteria_info(),
    }


def criteria_info():
    return {
        "낮음": f"제품 참고점수 {LEVEL_LOW_MAX}점 미만",
        "중간": f"{LEVEL_LOW_MAX}점 이상 {LEVEL_HIGH_MIN}점 미만",
        "높음": f"{LEVEL_HIGH_MIN}점 이상 또는 중대한 유해성(H350·H340·H360·H370·H372) 확인",
        "게이지최대": 200,
    }


# -----------------------------------------------------------------------------
# 성분 페이지
# -----------------------------------------------------------------------------
@app.get("/api/ingredients/search")
def search_ingredients(q: str = Query(""), limit: int = Query(30, ge=1, le=100)):
    keyword = normalize(q)
    items = []
    for aid, active in active_by_id.items():
        values = [active.get("한글명", ""), active.get("영문명", ""), active.get("CAS", "")]
        if keyword:
            rank = best_rank(values, [keyword])
            if rank == 99:
                continue
        else:
            rank = 0
        t = ingredient_tox(aid)
        t["제품수"] = product_count_by_active.get(aid, 0)
        t["_rank"] = rank
        items.append(t)

    items.sort(key=lambda x: (x["_rank"], -x["제품수"], x["한글명"]))
    summary = {"low": 0, "mid": 0, "high": 0, "none": 0}
    for x in items:
        summary[x["등급"]] += 1
    for x in items:
        x.pop("_rank", None)
        x.pop("H코드상세", None)
    return {"total": len(items), "summary": summary, "results": items[:limit]}


@app.get("/api/ingredients/{active_id}")
def get_ingredient(active_id: str):
    if str(active_id) not in active_by_id:
        raise HTTPException(status_code=404, detail="해당 성분을 찾을 수 없습니다.")
    products = []
    for pid, aids in active_ids_by_product.items():
        if str(active_id) in aids:
            row = product_by_id.get(pid)
            if row is not None:
                t = product_tox_cached(pid)
                products.append({
                    "등록번호": row.get("등록번호", ""), "상표명": row.get("상표명", ""),
                    "품목명": row.get("품목명", ""), "용도": row.get("용도", ""),
                    "제형": row.get("제형", ""), "인축독성": row.get("인축독성", ""),
                    "어독성": row.get("어독성", ""), "회사명": row.get("회사명", ""),
                    "독성": tox_brief(t),
                })
    products.sort(key=lambda x: (x["상표명"], x["등록번호"]))
    return {"active": ingredient_tox(active_id), "products": products}


# -----------------------------------------------------------------------------
# 농약 추천: 작물 + 병해충 → 농약 1개
#   1) 작물·병해충이 가장 정확하게 일치하는 사용등록 제품만 후보로 삼는다 (관련도 최고 구간).
#   2) 모든 원제의 인체건강 점수가 '확정'된 제품만 추천 대상으로 한다 (미확인 ≠ 안전).
#   3) 원제 참고점수 최고값 → 중대한 유해성 수 → 원제 점수 합 → 공식 인축독성 → 원제 수
#      → 안전사용시기(짧은 순) 순으로 가장 낮은 제품 1개를 고른다.
# -----------------------------------------------------------------------------
def explain_choice(winner_t, others_t, tie_count):
    reasons = []
    ref = winner_t.get("참고점수")
    if ref is not None:
        reasons.append(f"제품 참고점수는 {ref:.2f}점입니다.")
    if winner_t.get("중대한유해성"):
        reasons.append("중대한 유해성 코드가 확인되어 사용 전 라벨 확인이 필요합니다: " + ", ".join(winner_t["중대한유해성"]))
    else:
        reasons.append("확인된 원제 H-code에서 중대 유해성 코드가 확인되지 않았습니다.")
    reasons.append(f"공식 인축독성 등급은 {winner_t['인축독성']['표기']}입니다.")
    if tie_count > 1:
        reasons.append("동점 후보는 중대 유해성 여부 → 원제 유해성 구간 → 희석농도 → 원제 참고점수 등의 기준으로 추가 비교했습니다.")
    return reasons


@app.get("/api/recommend")
def recommend(crop: str = Query(""), pest: str = Query("")):
    if not (split_keywords(crop) or split_keywords(pest)):
        return {"found": False, "message": "작물 또는 병해충을 입력해주세요."}
    items = search_all("", crop, pest)
    if not items:
        return {"found": False, "message": "입력한 작물·병해충에 등록된 제품이 없습니다.", "candidate_count": 0}
    # 검색 관련도는 등록 적합성의 대체가 아님: 모든 매칭 등록 제품 비교
    pool = items
    chemical = [i for i in pool if not biological(i)]
    bio = [i for i in pool if biological(i)]
    # 생물농약과 화학농약을 같은 순위로 혼합하지 않는다.
    ranked = sorted(chemical, key=lambda p: decision_key(p, p))
    ranked_bio = sorted(bio, key=lambda p: (p.get("상표명", ""), p.get("등록번호", "")))
    selected = ranked[0] if ranked else (ranked_bio[0] if ranked_bio else None)
    if selected is None:
        return {"found": False, "message": "등록 제품이 없습니다."}
    t = product_tox(selected)
    single = len(pool) == 1
    has_missing = any(decision_info(p,p)["자료미확인"] for p in chemical)
    mode = ("정보만" if single else
            "생물농약 별도 기준" if not ranked else
            "정보만" if len(ranked) == 1 else
            "참고 순위" if has_missing else "추천 순위")
    reasons = [choice_reason((ranked[0],ranked[0]), (ranked[1],ranked[1]))] if len(ranked)>1 else ["비교 가능한 화학농약이 2개 미만이므로 상대 추천을 하지 않습니다."]
    if has_missing: reasons.append("원제·품목·희석농도 자료 미확인은 안전을 뜻하지 않습니다.")
    reasons.append(FIXED_NOTICE)
    return {"found": True, "crop": crop, "pest": pest, "candidate_count": len(pool),
            "eligible_count": len(ranked), "tie_count": 0, "ties": [], "mode": mode,
            "product": selected, "tox": {k:v for k,v in t.items() if k != "정렬키"},
            "decision": decision_info(selected,selected),
            "ranked": [{"등록번호":p.get("등록번호"),"상표명":p.get("상표명"),
                        "순위":j+1,"판단":decision_info(p,p)} for j,p in enumerate(ranked[:20])],
            "biological": [{"등록번호":p.get("등록번호"),"상표명":p.get("상표명")} for p in ranked_bio],
            "reasons": reasons, "notice": FIXED_NOTICE, "criteria": criteria_info()}


# -----------------------------------------------------------------------------
# 농약 비교: 후보 농약 이름 여러 개 (+ 작물/병해충) → 독성 비교 후 1개 선정
# -----------------------------------------------------------------------------
def resolve_candidate(query, crop, pest):
    """입력 하나를 대표 제품 하나로 해석한다. 작물/병해충이 있으면 그 조건에 사용등록된 제품을 우선."""
    q_keywords = split_keywords(query)
    crop_keywords = split_keywords(crop)
    pest_keywords = split_keywords(pest)
    has_use = bool(crop_keywords or pest_keywords)

    if query in product_by_reg:
        matched = [(100, product_by_reg[query])]
    else:
        matched = []
        for p in product_by_reg.values():
            s = product_search_score(p, q_keywords)
            if s >= 0:
                matched.append((s, p))
    if not matched:
        return None
    top = max(s for s, _ in matched)
    matched = [(s, p) for s, p in matched if s == top]

    rows = []
    for s, p in matched:
        usages = usage_by_reg.get(str(p.get("등록번호", "")), [])
        if has_use:
            us, usage = usage_match_and_score(usages, crop_keywords, pest_keywords)
        else:
            us, usage = 0, (usages[0] if usages else {})
        rows.append((us, p, usage))

    registered = [r for r in rows if r[0] >= 0] if has_use else rows
    pool = registered or rows
    pool.sort(key=lambda r: (-r[0], product_tox(r[1])["정렬키"], r[1].get("상표명", ""), r[1].get("등록번호", "")))
    us, p, usage = pool[0]
    return {
        "product": p, "usage": usage or {}, "usage_score": us,
        "registered": (us >= 0) if has_use else None,
        "same_name_count": len(matched),
    }


@app.get("/api/compare")
def compare(products: str = Query(""), crop: str = Query(""), pest: str = Query("")):
    queries = list(dict.fromkeys(x.strip() for x in re.split(r"[,\n]", products) if x.strip()))[:5]
    if len(queries) < 2:
        return {"found": False, "message": "비교할 농약을 2개 이상 입력해주세요."}
    has_use = bool(split_keywords(crop) or split_keywords(pest))
    entries = []
    for q in queries:
        r = resolve_candidate(q, crop, pest)
        if r is None:
            entries.append({"입력":q,"찾음":False}); continue
        p = r["product"]; t = product_tox(p)
        entries.append({"입력":q,"찾음":True,"등록번호":p.get("등록번호", ""),
            "상표명":p.get("상표명", ""),"품목명":p.get("품목명", ""),
            "동일명제품수":r["same_name_count"],"사용등록":r["registered"],
            "사용정보":{f:r["usage"].get(f, "") for f in USAGE_FIELDS},
            "tox":{k:v for k,v in t.items() if k != "정렬키"},
            "판단":decision_info(p,r["usage"]), "_product":p,"_usage":r["usage"]})
    valid = [e for e in entries if e.get("찾음") and e["사용등록"] is not False]
    chemical = sorted([e for e in valid if not e["판단"]["생물농약"]], key=lambda e:decision_key(e["_product"],e["_usage"]))
    bio = [e for e in valid if e["판단"]["생물농약"]]
    for j,e in enumerate(chemical):e["순위"]=j+1
    for e in bio:e["순위"]="생물농약 별도 기준"
    winner = chemical[0] if len(chemical)>=2 else None
    messages=[]
    if not has_use:messages.append("작물·병해충을 입력하지 않아 등록 적합성을 검증하지 않았습니다. 결과는 참고용입니다.")
    for e in entries:
        if not e.get("찾음"):messages.append(f"{e['입력']}: 제품 미확인")
        elif e["사용등록"] is False:messages.append(f"{e['상표명']}: 해당 작물·병해충 등록이 확인되지 않아 순위 제외")
        elif e["판단"]["자료미확인"]:messages.append(f"{e['상표명']}: 일부 유해성 자료 미확인(안전 의미 아님)")
    if bio:messages.append("생물농약/페로몬제/보조제는 화학농약과 동일 기준으로 순위를 매기지 않았습니다.")
    reasons = [choice_reason((chemical[0]["_product"],chemical[0]["_usage"]), (chemical[1]["_product"],chemical[1]["_usage"]))] if winner else ["화학농약 후보가 2개 미만이므로 추천 1개를 선정하지 않았습니다."]
    reasons.append(FIXED_NOTICE)
    for e in entries:
        e.pop("_product",None);e.pop("_usage",None)
    return {"found":True,"crop":crop,"pest":pest,"results":entries,
        "winner":winner["등록번호"] if winner else None,"reasons":reasons,
        "messages":messages,"notice":FIXED_NOTICE,"criteria":criteria_info()}


# -----------------------------------------------------------------------------
# 상세 화면 하단: 같은 방제 조건의 대안 제품 (독성이 낮은 순)
# -----------------------------------------------------------------------------
@app.get("/api/alternatives/{registration_number}")
def alternatives(registration_number: str, crop: str = Query(""), pest: str = Query(""), limit: int = Query(6, ge=1, le=12)):
    product = product_by_reg.get(str(registration_number))
    if product is None:
        raise HTTPException(status_code=404, detail="해당 농약 정보를 찾을 수 없습니다.")
    usages = usage_by_reg.get(str(registration_number), [])
    # 검색 조건이 전달되면 반드시 해당 제품의 동일 사용등록 행과 일치하는지 검증한다.
    if crop.strip() or pest.strip():
        score, matched = usage_match_and_score(usages, split_keywords(crop), split_keywords(pest))
        if score < 0 or matched is None:
            return {"results": [], "crop": crop, "pest": pest, "total": 0,
                    "message": "선택한 농약의 해당 작물·병해충 사용등록을 확인하지 못했습니다."}
        crop = matched.get("작물명", "") if crop.strip() else ""
        pest = matched.get("적용병해충", "") if pest.strip() else ""
    elif usages:
        crop, pest = usages[0].get("작물명", ""), usages[0].get("적용병해충", "")
    else:
        return {"results": [], "crop": "", "pest": "", "total": 0,
                "message": "이 제품의 사용등록 정보가 없습니다."}
    items = [i for i in search_all("", crop, pest) if str(i.get("등록번호", "")) != str(registration_number)]
    if not items:
        return {"results": [], "crop": crop, "pest": pest, "total": 0}
    # 관련도 최고 그룹만 선택하면 등록된 대안이 누락되므로 전체 후보 사용
    items.sort(key=lambda i: (biological(i), decision_key(i, i)))
    return {"crop": crop, "pest": pest, "total": len(items), "results": attach_tox(items[:limit]), "notice": FIXED_NOTICE}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
