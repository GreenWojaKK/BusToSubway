"""ulsan_bus_route_before.parquet 로더 (stage1_canonical_spec.md §3.3).

한국어 헤더 10컬럼, ID 컬럼 없음(정류장명+좌표뿐) — stop 해소는 s00 build가 표준 조인 함수로 수행.
시각은 'H시 M분 S초' 파이프(|) 구분(4~25시 연장 표기) — 토큰 파서(kr_hms_to_sec)는 이 파일 소속.
불변식은 감사 실측(reference/audit/routes_stops.md §9)의 이식 — 위반 시 ContractViolation.

주의(검증 규칙): 이 파일의 정류장열은 trip 패턴들의 **합집합 마스터**다 — canonical 시퀀스
원천으로 읽지 않는다(산출물명 route_union이 오용 방지 — design.md §5 s00).
아래 상수는 감사 실측 기준값이다(임계값 아님, 출처 [RS§n]).
"""
from __future__ import annotations

import re

import pandas as pd

import bts.paths as paths
from bts.io import ContractViolation
from bts.io import timeparse

_FILE = "ulsan_bus_route_before.parquet"
_COLUMNS = ["노선명", "정류장순서", "원본순서", "정류장명", "위도", "경도",
            "행정동", "도착시간들", "출발시간들", "도착횟수"]   # [RS§1] 순서 고정
_ROWS = 21_402             # [RS§1]
_ROUTE_NAMES = 400         # [RS§3] schedule 398 + {50(내고산 방면), 김해공항}
_STOP_UNIVERSE = 3_409     # [RS§5] 유니크 (정류장명,위도,경도) == stops_before 행수
_KR_HMS_RE = re.compile(r"^(\d{1,2})시 (\d{1,2})분 (\d{1,2})초$")   # [RS§7]
_LIST_SEP = "|"            # [RS§7] 시각 리스트 구분자


def kr_hms_to_sec(tok: str) -> int:
    """'H시 M분 S초' 토큰 → 초. 4~25시 연장 표기 허용 — service_s 창 [4h,26h) 확인.

    이 파일 고유 포맷의 파서라 여기 소속이다(after의 'H시 M분 S초' 겸용 파서와 별개).
    """
    m = _KR_HMS_RE.match(str(tok).strip())
    if not m:
        raise ValueError(f"'H시 M분 S초' 형식 위반: {tok!r}")
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if mi >= timeparse.SEC_PER_MIN or se >= timeparse.SEC_PER_MIN:
        raise ValueError(f"분/초 범위 위반: {tok!r}")
    total = h * timeparse.SEC_PER_HOUR + mi * timeparse.SEC_PER_MIN + se
    t = paths.load_params()["time"]
    lo = t["service_min_h"] * timeparse.SEC_PER_HOUR
    hi = t["service_max_h"] * timeparse.SEC_PER_HOUR
    if not (lo <= total < hi):
        raise ValueError(f"service_s 창 [{lo},{hi}) 밖: {tok!r} → {total}")
    return total


def split_time_list(cell: str) -> list[str]:
    """파이프 구분 시각 리스트 셀 → 토큰 리스트(문자열 — 초 변환은 호출측)."""
    return str(cell).split(_LIST_SEP)


def _fail(msg: str) -> None:
    raise ContractViolation(f"bus_route_before: {msg}")


def validate(df: pd.DataFrame, meta: dict | None = None) -> pd.DataFrame:
    """raw 불변식 assert. meta(dict)에 실측 관찰값을 기록한다."""
    m = meta if meta is not None else {}
    if df.columns[0].startswith("﻿"):
        _fail("BOM 포함 컬럼명 — utf-8-sig 로딩 실패 [RS§1]")
    if list(df.columns) != _COLUMNS:
        _fail(f"컬럼 순서 불일치 {list(df.columns)} [RS§1]")
    m["bom_ok"] = True
    m["columns_ok"] = True

    m["rows"] = int(len(df))
    if len(df) != _ROWS:
        _fail(f"행수 {len(df)} != {_ROWS} [RS§1]")

    if df.duplicated(["노선명", "정류장순서"]).any():
        _fail("(노선명, 정류장순서) 중복 [RS§9]")

    m["route_names"] = int(df["노선명"].nunique())
    if m["route_names"] != _ROUTE_NAMES:
        _fail(f"노선명 고유수 {m['route_names']} != {_ROUTE_NAMES} [RS§3]")

    # 노선명별 정류장순서 1..N 연속 [RS§9]
    seq = df["정류장순서"].astype(int)
    g = seq.groupby(df["노선명"])
    if not ((g.min() == 1) & (g.max() == g.size())).all():
        _fail("노선명별 정류장순서 1..N 연속 위반 [RS§9]")

    # 결측: 행정동만 허용(울산 밖 정류장 — [RS§1]), 그 외 0
    non_dong_null = int(df.drop(columns=["행정동"]).isna().sum().sum())
    m["non_dong_null"] = non_dong_null
    if non_dong_null:
        _fail(f"행정동 외 컬럼 결측 {non_dong_null} != 0 [RS§1]")

    # 도착횟수 == 시각 토큰 수 (도착·출발 모두 — 전행) [RS§9]
    cnt = df["도착횟수"].astype(int)
    arr_n = df["도착시간들"].str.split(_LIST_SEP, regex=False).str.len()
    dep_n = df["출발시간들"].str.split(_LIST_SEP, regex=False).str.len()
    m["token_count_mismatch"] = int(((arr_n != cnt) | (dep_n != cnt)).sum())
    if m["token_count_mismatch"]:
        _fail("도착횟수 != 시각 토큰 수 [RS§9]")

    # 유니크 (정류장명,위도,경도) == stop 우주 3,409 [RS§5]
    m["unique_name_coord"] = int(len(df[["정류장명", "위도", "경도"]].drop_duplicates()))
    if m["unique_name_coord"] != _STOP_UNIVERSE:
        _fail(f"유니크 (정류장명,위도,경도) {m['unique_name_coord']} != {_STOP_UNIVERSE} [RS§5]")
    return df


def load(meta: dict | None = None) -> pd.DataFrame:
    """raw 로드 + 불변식 검증. meta(dict 전달 시)에 관찰값 기록."""
    df = pd.read_parquet(paths.raw_path(_FILE))
    return validate(df, meta)
