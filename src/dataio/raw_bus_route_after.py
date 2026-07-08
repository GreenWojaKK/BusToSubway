"""bus_route_after 로더 — data/ulsan_bus_route_after.parquet (stage1_canonical_spec.md §3.3).

schedule_after와 같은 날 로그의 (route_id, stop) 집계본 17,841행 [SA§6.3] —
독립 검증 자료가 아니다(순환 논증 주의; s00이 derived_from_schedule=True로 표식).
로더는 raw 형태 그대로(원 컬럼명) 반환하되, raw 불변식(audit/routes_stops.md §9)을
로딩과 동시에 assert한다 — 위반 시 ContractViolation.

시각 겸용 파서(HH:MM:SS / 'H시 M분 S초')는 이 파일 소속이다(design.md §3) —
service_s 변환(wrap)은 s00 build가 params(time.service_min_h)로 수행한다.
"""
from __future__ import annotations

import re

import pandas as pd

import paths
from dataio import ContractViolation
from dataio.timeparse import SEC_PER_HOUR, SEC_PER_MIN

FILENAME = "ulsan_bus_route_after.parquet"

# ── 감사 실측 기준 상수 (audit/routes_stops.md §1·§2·§7·§9) ──────────────────
# 원시 파일 검증 규칙(튜닝 대상 아님). 정위치는 params.yaml raw_contracts 절이나
# 그 파일은 인프라 소유라 로더 기준 상수로 둔다.
ROWS = 17_841                        # [RS§1]
COLUMNS = ["route_id", "route_name", "stop_sequence", "stop_name",
           "stop_lat", "stop_lon", "권역", "행정동", "도착시간들", "stop_id", "도착횟수"]
N_PATTERNS = 348                     # [RS§9-3] route_id 수
N_ROUTE_NAMES = 184                  # [RS§9-3]
KR_FORMAT_ROWS = 2_318               # [RS§7] 도착횟수==1 행 — 전부 'H시 M분 S초'
HMS_FORMAT_ROWS = 15_523             # [RS§7] 나머지 — 전부 HH:MM:SS (혼합 행 0)

# 시각 토큰 2포맷 (after는 0~23시 wrap 표기 — before의 24+시 연장과 규약 상이 [RS§7])
HMS_TOKEN_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d):([0-5]\d)$")
KR_TOKEN_RE = re.compile(r"^(\d{1,2})시 (\d{1,2})분 (\d{1,2})초$")
_H_MAX, _MS_MAX = 23, 59             # 한국어 포맷 필드 상한 (시계 단위 정의 — 임계값 아님)


def parse_time_token(tok: str) -> int:
    """도착시간들 토큰 1개 → 자정 기준 초 (0~23시 원표기 그대로 — wrap 없음).

    'HH:MM:SS'와 'H시 M분 S초' 겸용 [RS§7]. 두 포맷 외 이형은 ValueError.
    service_s 변환(새벽 wrap +86400)은 호출측(s00 build)이 params로 수행한다.
    """
    s = str(tok).strip()
    m = HMS_TOKEN_RE.match(s)
    if m:
        return int(m.group(1)) * SEC_PER_HOUR + int(m.group(2)) * SEC_PER_MIN + int(m.group(3))
    m = KR_TOKEN_RE.match(s)
    if m:
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if h > _H_MAX or mi > _MS_MAX or se > _MS_MAX:
            raise ValueError(f"한국어 시각 토큰 필드 범위 위반: {tok!r}")
        return h * SEC_PER_HOUR + mi * SEC_PER_MIN + se
    raise ValueError(f"시각 토큰 이형(두 포맷 외): {tok!r}")


def load() -> pd.DataFrame:
    """raw 불변식 전건 assert 후 원 컬럼명 그대로 반환 (dtype=str)."""
    df = pd.read_parquet(paths.raw_path(FILENAME))

    # 1) BOM·컬럼·행수 [RS§9-1,3]
    if df.columns[0].startswith("﻿"):
        raise ContractViolation(f"{FILENAME}: BOM 포함 컬럼명 — utf-8-sig 로딩 실패")
    if list(df.columns) != COLUMNS:
        raise ContractViolation(f"{FILENAME}: 컬럼 불일치 {list(df.columns)}")
    if len(df) != ROWS:
        raise ContractViolation(f"{FILENAME}: 행수 {len(df)} != {ROWS}")
    if int(df.isna().sum().sum()):
        raise ContractViolation(f"{FILENAME}: 결측 존재 (감사 실측 0 [RS§1])")

    # 2) 키·연속성·함수성 [RS§9-3]
    if df["route_id"].nunique() != N_PATTERNS or df["route_name"].nunique() != N_ROUTE_NAMES:
        raise ContractViolation(
            f"{FILENAME}: route_id {df['route_id'].nunique()} != {N_PATTERNS} 또는 "
            f"route_name {df['route_name'].nunique()} != {N_ROUTE_NAMES}")
    if int(df.duplicated(["route_id", "stop_sequence"]).sum()):
        raise ContractViolation(f"{FILENAME}: (route_id, stop_sequence) 중복")
    seq = df["stop_sequence"].astype(int)
    g = seq.groupby(df["route_id"])
    if not bool(((g.min() == 1) & (g.max() == g.size())).all()):
        raise ContractViolation(f"{FILENAME}: stop_sequence 1..N 연속 위반")
    if int((df.groupby("route_id")["route_name"].nunique() > 1).sum()):
        raise ContractViolation(f"{FILENAME}: route_id→route_name 함수성 위반")

    # 3) 도착횟수 == 시각 토큰 수 전행 [RS§9-10]
    n_tokens = df["도착시간들"].str.split("|", regex=False).str.len()
    if int((n_tokens != df["도착횟수"].astype(int)).sum()):
        raise ContractViolation(f"{FILENAME}: 도착횟수 != 시각 토큰 수 행 존재")

    # 4) 시각 포맷 이원성: 도착횟수==1 행 전부 한국어, 나머지 전부 HH:MM:SS [RS§7]
    single = df["도착횟수"] == "1"
    kr_rows = df.loc[single, "도착시간들"]
    hms_rows = df.loc[~single, "도착시간들"]
    if len(kr_rows) != KR_FORMAT_ROWS or len(hms_rows) != HMS_FORMAT_ROWS:
        raise ContractViolation(
            f"{FILENAME}: 포맷 분기 행수 {len(kr_rows)}/{len(hms_rows)} != "
            f"{KR_FORMAT_ROWS}/{HMS_FORMAT_ROWS}")
    if not bool(kr_rows.str.fullmatch(KR_TOKEN_RE.pattern).all()):
        raise ContractViolation(f"{FILENAME}: 도착횟수==1 행에 한국어 포맷 아닌 값 존재")
    tokens = hms_rows.str.split("|", regex=False).explode()
    if not bool(tokens.str.fullmatch(HMS_TOKEN_RE.pattern).all()):
        raise ContractViolation(f"{FILENAME}: HH:MM:SS 아닌 토큰 존재 (혼합 행?)")

    # 5) 좌표 품질 — bbox 밖 0건 실측 [RS§6] (bbox는 stops_after와 동일 기준: params)
    c = paths.load_params()["raw_contracts"]["stops_after"]
    lat = df["stop_lat"].astype(float)
    lon = df["stop_lon"].astype(float)
    if lat.isna().any() or lon.isna().any() or (lat == 0).any() or (lon == 0).any():
        raise ContractViolation(f"{FILENAME}: 좌표 NaN/0 존재")
    bad = int(((lat < c["lat_min"]) | (lat > c["lat_max"])
               | (lon < c["lon_min"]) | (lon > c["lon_max"])).sum())
    if bad:
        raise ContractViolation(f"{FILENAME}: 좌표 bbox 밖 {bad}행 (실측 0 [RS§6])")

    return df
