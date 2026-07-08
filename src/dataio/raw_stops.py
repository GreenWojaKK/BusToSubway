"""stops 로더 — before/after (stage1_canonical_spec.md §3.3).

로더는 raw 형태 그대로(원 컬럼명) 반환하되, 자기 파일의 raw 불변식을 로딩과
동시에 assert한다 — 하나라도 깨지면 ContractViolation(DataFrame 미반환).
stops_after의 '.0' float 문자열 정규화는 **여기** 소속이다(파일 오귀속 함정 — design.md §13).
수치 불변식은 params.yaml(raw_contracts) — 감사 실측치, 튜닝 대상 아님.
"""
from __future__ import annotations

import pandas as pd

import paths
from dataio import ContractViolation

_AFTER_FLOAT_RE = r"\d+\.0"   # stops_after stop_id 전행 '^\d+\.0$' [RS§5]


def _read(name: str) -> pd.DataFrame:
    return pd.read_parquet(paths.raw_path(name))


def _assert_coords(df: pd.DataFrame, name: str, c: dict) -> None:
    lat = df["stop_lat"].astype(float)
    lon = df["stop_lon"].astype(float)
    if lat.isna().any() or lon.isna().any() or (lat == 0).any() or (lon == 0).any():
        raise ContractViolation(f"{name}: 좌표 NaN/0 존재")
    bad = ((lat < c["lat_min"]) | (lat > c["lat_max"])
           | (lon < c["lon_min"]) | (lon > c["lon_max"])).sum()
    if bad:
        raise ContractViolation(
            f"{name}: 좌표 실측 범위 밖 {bad}행 (lat [{c['lat_min']},{c['lat_max']}], "
            f"lon [{c['lon_min']},{c['lon_max']}]) — 감사 [RS§6] 위반")


def load_before() -> pd.DataFrame:
    """ulsan_stops_before.parquet — 컬럼 stop_id, stop_name, stop_lat, stop_lon."""
    c = paths.load_params()["raw_contracts"]["stops_before"]
    df = _read("ulsan_stops_before.parquet")
    expected_cols = ["stop_id", "stop_name", "stop_lat", "stop_lon"]
    if list(df.columns) != expected_cols:
        raise ContractViolation(f"stops_before: 컬럼 불일치 {list(df.columns)}")
    if len(df) != c["rows"]:
        raise ContractViolation(f"stops_before: 행수 {len(df)} != {c['rows']}")
    if df["stop_id"].duplicated().any():
        raise ContractViolation("stops_before: stop_id 중복")
    _assert_coords(df, "stops_before", c)
    return df


def load_after() -> pd.DataFrame:
    """ulsan_stops_after.parquet — '.0' float 문자열 잔재를 여기서 strip 정규화한다.

    컬럼: stop_id, stop_name, stop_lat, stop_lon, zone, admin_dong.
    검증 규칙: 전행 `^\\d+\\.0$` 검증 후 strip → 유일 (감사 [RS§5], [SA§6.1]).
    """
    c = paths.load_params()["raw_contracts"]["stops_after"]
    df = _read("ulsan_stops_after.parquet")
    expected_cols = ["stop_id", "stop_name", "stop_lat", "stop_lon", "zone", "admin_dong"]
    if list(df.columns) != expected_cols:
        raise ContractViolation(f"stops_after: 컬럼 불일치 {list(df.columns)}")
    if len(df) != c["rows"]:
        raise ContractViolation(f"stops_after: 행수 {len(df)} != {c['rows']}")
    if not df["stop_id"].str.fullmatch(_AFTER_FLOAT_RE).all():
        raise ContractViolation("stops_after: stop_id 전행 '^\\d+\\.0$' 검증 규칙 위반")
    df["stop_id"] = df["stop_id"].str.replace(r"\.0$", "", regex=True)
    if df["stop_id"].duplicated().any():
        raise ContractViolation("stops_after: '.0' strip 후 stop_id 중복")
    _assert_coords(df, "stops_after", c)
    return df
