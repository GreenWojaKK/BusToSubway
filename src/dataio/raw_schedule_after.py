"""schedule_after 로더 — data/ulsan_route_schedule_after.parquet (stage1_canonical_spec.md §3.3).

2025-05-06(화) 단 하루의 OBE(차량 단말) 실운행 도착 로그 280,797행.
로더는 raw 형태 그대로(원 컬럼명) 반환하되, 자기 파일의 raw 불변식
(audit/schedule_after.md §8)을 로딩과 동시에 assert한다 — 위반 시 ContractViolation.

시각의 '파싱'(오전/오후 → service_s)은 s00 build 소속이다 — 여기서는 포맷 검증만 한다.
`route_id`/`stop_id` 컬럼명은 io 패키지 내부에서만 존재한다(개명은 s00 build).
"""
from __future__ import annotations

import pandas as pd

import paths
from dataio import ContractViolation
from dataio.timeparse import KR_TS_RE, SENTINELS

FILENAME = "ulsan_route_schedule_after.parquet"

# ── 감사 실측 기준 상수 (audit/schedule_after.md §1·§4·§6·§8) ────────────────
# 임계값이 아니라 원시 파일의 검증 규칙이다(raw sha256 기준값과 동위) — 튜닝 대상 아님.
# 정위치는 params.yaml raw_contracts 절이나, 그 파일은 인프라 소유라 로더 기준 상수로 둔다.
ROWS = 280_797                       # [SA§1] shape (280,797, 14)
COLUMNS = ["운행 일자", "OBE_ID", "route_id", "route_name", "버스정류장 순번",
           "stop_id_raw", "stop_name", "stop_id", "arrival_time", "departure_time",
           "위도", "경도", "권역", "행정동"]          # [SA§1] 첫 컬럼명에 공백 포함
SERVICE_DATE = "20250506"            # [SA§2] 운행일자 nunique==1
SENTINEL_COUNTS = {"0001-01-01": 8_302, "2025-05-07 0:00": 1}   # [SA§4] 숨은 결측
NAMELESS_PATTERNS = frozenset({"196000190", "194001282", "194001281"})  # [SA§6.2]
NAMELESS_ROWS = 716                  # [SA§1] route_name 결측 행수
MASTER_GRID_COMBOS = 18_106          # [SA§5.4] (route_id, 순번)→stop_id 1:1 격자


def load() -> pd.DataFrame:
    """raw 불변식 전건 assert 후 원 컬럼명 그대로 반환 (dtype=str).

    자정 넘김은 timestamp 날짜부가 05-07로 넘어가는 방식이므로(운행일자는 20250506 고정)
    원본 행 순서에 의존하지 말 것 — timestamp 재정렬은 s00 build가 수행한다 [SA§5.1].
    """
    df = pd.read_parquet(paths.raw_path(FILENAME))

    # 1) BOM·컬럼 [SA§8-1,2]
    if df.columns[0].startswith("﻿"):
        raise ContractViolation(f"{FILENAME}: BOM 포함 컬럼명 — utf-8-sig 로딩 실패")
    if list(df.columns) != COLUMNS:
        raise ContractViolation(f"{FILENAME}: 컬럼 불일치 {list(df.columns)}")
    if len(df) != ROWS:
        raise ContractViolation(f"{FILENAME}: 행수 {len(df)} != {ROWS}")

    # 2) 운행일자 단일값 [SA§8-3]
    dates = df["운행 일자"].unique()
    if len(dates) != 1 or dates[0] != SERVICE_DATE:
        raise ContractViolation(f"{FILENAME}: 운행 일자 검증 규칙 위반 {dates[:5]}")

    # 3) arrival 전행 오전/오후 regex [SA§8-4]
    arr_bad = int((~df["arrival_time"].str.fullmatch(KR_TS_RE.pattern)).sum())
    if arr_bad:
        raise ContractViolation(f"{FILENAME}: arrival_time 포맷 불일치 {arr_bad}행")

    # 4) departure == regex or sentinel(정확히 8,302 + 1) — 그 외 이형 0 [SA§8-5]
    dep = df["departure_time"]
    dep_re = dep.str.fullmatch(KR_TS_RE.pattern)
    observed_sentinels = dep[~dep_re].value_counts().to_dict()
    if observed_sentinels != SENTINEL_COUNTS:
        raise ContractViolation(
            f"{FILENAME}: departure sentinel 검증 규칙 위반 — 관측 {observed_sentinels} "
            f"!= 기대 {SENTINEL_COUNTS}")
    if set(observed_sentinels) - set(SENTINELS):
        raise ContractViolation(
            f"{FILENAME}: timeparse.SENTINELS 미등재 sentinel {observed_sentinels}")

    # 5) route_name 결측 = 정확히 716행, 전부 이름미상 3 pattern 소속 [SA§8-11]
    nameless = df.loc[df["route_name"].isna(), "route_id"]
    if len(nameless) != NAMELESS_ROWS or not set(nameless).issubset(NAMELESS_PATTERNS):
        raise ContractViolation(
            f"{FILENAME}: route_name 결측 검증 규칙 위반 — {len(nameless)}행, "
            f"pattern {sorted(set(nameless))[:5]}")

    # 6) (route_id, 순번)→stop_id 완전 1:1 격자 [SA§8-7]
    grid = df.groupby(["route_id", "버스정류장 순번"])["stop_id"].nunique()
    if len(grid) != MASTER_GRID_COMBOS or int((grid > 1).sum()):
        raise ContractViolation(
            f"{FILENAME}: 마스터 격자 1:1 위반 — 조합 {len(grid)}, "
            f"충돌 {int((grid > 1).sum())}")

    # 7) 원시 자연키 유일 [SA§8-8] (완전 중복 0을 함의)
    dup = int(df.duplicated(["OBE_ID", "route_id", "버스정류장 순번", "arrival_time"]).sum())
    if dup:
        raise ContractViolation(f"{FILENAME}: 자연키 중복 {dup}행")

    return df
