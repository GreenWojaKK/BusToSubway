"""ulsan_route_schedule_before.parquet 로더 (stage1_canonical_spec.md §3.3).

utf-8-sig·dtype=str 고정. 전결측 48행(단일 연속 블록) 검증 후 제거 — 부분결측 0 확인.
raw 컬럼명 유지(개명 route_id→pattern_id 등은 s00 build 소속), 시각은 문자열 형식 검증까지만.
불변식은 감사 실측(reference/audit/schedule_before.md §10)의 이식 — 위반 시 ContractViolation.

아래 상수는 감사 실측 기준값이다(임계값 아님 — 튜닝 대상 아님, 출처 [SB§n]).
"""
from __future__ import annotations

import pandas as pd

import bts.paths as paths
from bts.io import ContractViolation

_FILE = "ulsan_route_schedule_before.parquet"
_COLUMNS = ["route_name", "stop_name", "stop_sequence", "arrival_time",
            "departure_time", "route_id", "trip_id", "stop_id"]   # [SB§1] 순서 고정
_RAW_ROWS = 427_527        # [SB§1] 헤더 제외 데이터 행
_ALLNULL_ROWS = 48         # [SB§7.1] 전결측 행 — 단일 연속 블록
_VALID_ROWS = 427_479      # [SB§1] 제거 후 유효 행
_TRIP_IDS = 7_625          # [SB§2]
_PATTERN_IDS = 487         # [SB§2] raw route_id = 방향·변형 패턴 단위
# [SB§10] 주의: 감사 표기 'BR_TAGO_USB+12자리'는 'BR_TAGO_' 뒤 12자(USB+숫자9) 표기다 —
# 실측 총 20자 = BR_TAGO_USB + 숫자 9자리 (variant_tags.md §1 '20자'와 정합).
_ROUTE_ID_RE = r"BR_(TAGO_USB\d{9}|ACC0_\d{8})"
_STOP_ID_RE = r"BS_(TAGO_USB\d{9}|ACC0_\d{8})"     # [SB§10] 총 20자
_HMS_RE = r"\d{1,2}:[0-5]\d:[0-5]\d"               # [SB§4] zero-pad 없음·24+시 표기
_TRIP_ORD_RE = r"_Ord\d{3}"                        # [SB§2] trip_id = route_id+_Ord+3자리


def _fail(msg: str) -> None:
    raise ContractViolation(f"schedule_before: {msg}")


def validate(df: pd.DataFrame, meta: dict | None = None) -> pd.DataFrame:
    """raw 불변식 assert + 전결측 블록 제거. meta(dict)에 실측 관찰값을 기록한다.

    반환: 전결측 48행 제거된 유효 427,479행 DataFrame(문자열 유지).
    """
    m = meta if meta is not None else {}
    if df.columns[0].startswith("﻿"):
        _fail("BOM 오염 컬럼명 — utf-8-sig 로딩 실패 [SB§7.2]")
    if list(df.columns) != _COLUMNS:
        _fail(f"컬럼 순서 불일치 {list(df.columns)} [SB§1]")
    m["bom_ok"] = True
    m["columns_ok"] = True

    m["raw_rows"] = int(len(df))
    if len(df) != _RAW_ROWS:
        _fail(f"원시 행수 {len(df)} != {_RAW_ROWS} [SB§1]")

    allnull = df.isna().all(axis=1)
    anynull = df.isna().any(axis=1)
    m["allnull_rows"] = int(allnull.sum())
    m["partial_null_rows"] = int((anynull & ~allnull).sum())
    idx = pd.Series(df.index[allnull])
    m["allnull_contiguous"] = bool((idx.diff().dropna() == 1).all()) if len(idx) else True
    if m["allnull_rows"] != _ALLNULL_ROWS:
        _fail(f"전결측 행 {m['allnull_rows']} != {_ALLNULL_ROWS} [SB§7.1]")
    if m["partial_null_rows"]:
        _fail(f"부분결측 행 {m['partial_null_rows']} != 0 [SB§1]")
    if not m["allnull_contiguous"]:
        _fail("전결측 48행이 단일 연속 블록이 아님 [SB§7.1]")

    df = df[~allnull].reset_index(drop=True)
    m["valid_rows"] = int(len(df))
    if len(df) != _VALID_ROWS:
        _fail(f"유효 행수 {len(df)} != {_VALID_ROWS} [SB§1]")

    m["dup_full_rows"] = int(df.duplicated().sum())
    if m["dup_full_rows"]:
        _fail(f"완전 중복 행 {m['dup_full_rows']} != 0 [SB§1]")
    m["dup_key_rows"] = int(df.duplicated(["trip_id", "stop_sequence"]).sum())
    if m["dup_key_rows"]:
        _fail(f"(trip_id, stop_sequence) 중복 {m['dup_key_rows']} != 0 [SB§2]")

    m["trip_ids"] = int(df["trip_id"].nunique())
    m["pattern_ids"] = int(df["route_id"].nunique())
    if m["trip_ids"] != _TRIP_IDS:
        _fail(f"trip_id 고유수 {m['trip_ids']} != {_TRIP_IDS} [SB§2]")
    if m["pattern_ids"] != _PATTERN_IDS:
        _fail(f"route_id 고유수 {m['pattern_ids']} != {_PATTERN_IDS} [SB§2]")

    # 함수적 의존: route_id→route_name, stop_id→stop_name (이름은 표시용 — 키는 id)
    m["route_name_functional_violations"] = int(
        (df.groupby("route_id")["route_name"].nunique() > 1).sum())
    if m["route_name_functional_violations"]:
        _fail("route_id→route_name 함수적 의존 위반 [SB§2]")
    m["stop_name_functional_violations"] = int(
        (df.groupby("stop_id")["stop_name"].nunique() > 1).sum())
    if m["stop_name_functional_violations"]:
        _fail("stop_id→stop_name 함수적 의존 위반 [SB§10]")

    # ID·trip_id 형식 (2계보 regex — TAGO/ACC0)
    m["route_id_regex_mismatch"] = int((~df["route_id"].str.fullmatch(_ROUTE_ID_RE)).sum())
    m["stop_id_regex_mismatch"] = int((~df["stop_id"].str.fullmatch(_STOP_ID_RE)).sum())
    if m["route_id_regex_mismatch"] or m["stop_id_regex_mismatch"]:
        _fail("ID regex 위반 [SB§10]")
    trip_prefix = df["trip_id"].str.replace(_TRIP_ORD_RE + r"$", "", regex=True)
    m["trip_id_format_mismatch"] = int(
        (~df["trip_id"].str.fullmatch(_ROUTE_ID_RE + _TRIP_ORD_RE)).sum()
        + (trip_prefix != df["route_id"]).sum())
    if m["trip_id_format_mismatch"]:
        _fail("trip_id != route_id+_Ord+3자리 [SB§2]")

    # 시각 문자열 형식 (zero-pad 없음·24+시 — 초 변환·창 검증은 s00 build/체크 소속)
    m["time_format_mismatch"] = int(
        (~df["arrival_time"].str.fullmatch(_HMS_RE)).sum()
        + (~df["departure_time"].str.fullmatch(_HMS_RE)).sum())
    if m["time_format_mismatch"]:
        _fail("시각 형식 위반 [SB§4]")
    return df


def load(meta: dict | None = None) -> pd.DataFrame:
    """raw 로드 + 불변식 검증. meta(dict 전달 시)에 관찰값 기록."""
    df = pd.read_parquet(paths.raw_path(_FILE))
    return validate(df, meta)
