"""base명 추출·이름 정규화·표준 조인 함수 (design.md §2.2, §2.6).

이름 조인은 이 모듈의 resolve_stops_by_name을 통해서만 허용된다 —
그 밖의 이름 키 조인·좌표 exact 문자열 조인은 리뷰 거부 대상이다
(좌표 exact 조인은 반올림 자릿수 혼재로 63.5%만 성공 — 감사 실측 [RS§5]).
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

import paths

# base 추출: route_name에서 마지막 괄호군 greedy 제거 + strip **만** 사용.
# pattern_id 숫자 절단 파싱 금지 — base 313·912는 stem이 2개(감사 실측 반증).
BASE_RE = re.compile(r"\(.*\)\s*$")   # greedy — 중첩 괄호('924 지원2 (문수초지원(오후))') 대응

_EARTH_R_M = 6371008.8   # 지구 평균 반경 (물리 상수 — 임계값 아님)


def base_route_name(route_name: str) -> str:
    """'837(태화강역방면)' → '837'. BASE_RE 제거 + strip만."""
    return BASE_RE.sub("", str(route_name)).strip()


def normalize_stop_name(name: str) -> str:
    """v1: strip()만 — 항등 근사가 검증 규칙이다(변경은 검증 규칙 리뷰 대상).

    place 1,759 == stop_name 유니크 1,759 실측은 "정규화가 항등에 가깝다"는 신호(design.md §5 s02).
    """
    return str(name).strip()


def load_aliases(path=None) -> dict[str, str]:
    """config/overrides/name_aliases.csv → {raw_name: canonical_name}."""
    p = path or (paths.CONFIG / "overrides" / "name_aliases.csv")
    df = pd.read_csv(p, encoding="utf-8-sig", dtype=str)
    return dict(zip(df["raw_name"], df["canonical_name"]))


def haversine_m(lat1, lon1, lat2, lon2):
    """haversine 거리(m) — 스칼라/ndarray 겸용."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float))
                              for x in (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    d = 2 * _EARTH_R_M * np.arcsin(np.sqrt(a))
    return float(d) if np.ndim(d) == 0 else d


def resolve_stops_by_name(df: pd.DataFrame, stops: pd.DataFrame,
                          name_col: str, lat_col: str, lon_col: str,
                          max_dist_m: float | None = None,
                          aliases: dict[str, str] | None = None):
    """이름과 좌표로 stop_id를 해소하는 표준 경로 (design.md §2.6).

    ① name_col에 alias 적용(적용 행수 반환 — before 검증 규칙: 정확히 1건)
    ② 정규화 이름 완전 일치 blocking
    ③ 같은 이름 내 haversine 최근접 stop 선택
    ④ 최근접 > max_dist_m 행은 실패 프레임으로 반환(해소 후 0이 검증 규칙; 실측 max 0.91m)

    stops 요구 컬럼: stop_id, stop_name, lat, lon.
    반환: (stop_id Series — df.index 정렬, 실패 행 DataFrame(+reason, min_dist_m), alias 적용 수).
    좌표 exact 문자열 조인 금지.
    """
    if max_dist_m is None:
        max_dist_m = paths.load_params()["join"]["max_dist_m"]
    aliases = load_aliases() if aliases is None else aliases

    names = df[name_col].astype(str).map(normalize_stop_name)
    alias_mask = names.isin(aliases.keys())
    n_alias = int(alias_mask.sum())
    names = names.where(~alias_mask, names.map(aliases))

    stop_names_norm = stops["stop_name"].astype(str).map(normalize_stop_name)
    groups: dict[str, pd.DataFrame] = {
        n: g for n, g in stops.assign(_n=stop_names_norm).groupby("_n")}

    resolved = pd.Series(index=df.index, dtype=object)
    fail_rows = []
    lat = df[lat_col].astype(float)
    lon = df[lon_col].astype(float)
    for idx in df.index:
        g = groups.get(names.at[idx])
        if g is None:
            fail_rows.append((idx, "no_name_match", math.nan))
            continue
        d = haversine_m(lat.at[idx], lon.at[idx],
                        g["lat"].astype(float).values, g["lon"].astype(float).values)
        d = np.atleast_1d(d)
        j = int(np.argmin(d))
        if d[j] > max_dist_m:
            fail_rows.append((idx, "too_far", float(d[j])))
            continue
        resolved.at[idx] = g["stop_id"].iloc[j]

    if fail_rows:
        fidx = [i for i, _, _ in fail_rows]
        failures = df.loc[fidx].copy()
        failures["reason"] = [r for _, r, _ in fail_rows]
        failures["min_dist_m"] = [d for _, _, d in fail_rows]
    else:
        failures = df.iloc[0:0].copy()
        failures["reason"] = pd.Series(dtype=str)
        failures["min_dist_m"] = pd.Series(dtype=float)
    return resolved, failures, n_alias
