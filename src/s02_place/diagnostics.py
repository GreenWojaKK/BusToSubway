"""s02_place 진단 — 제안일 뿐, 자동 병합 아님 (stage2_place_hub_spec.md §4.3).

diag_under_merge: 같은 name_norm의 분리 place 쌍(= linkage에서 병합되지 않은 쌍) 전량
  (또는 params.diag_under_merge_max_m 이내) — override merge 행 작성의 후보표.
diag_alias: 다른 name_norm의 근접 place 쌍(최근접 stop 쌍 ≤ params.diag_alias_max_m).
  자동 병합하지 않으며, 이 표는 place_overrides 작성 후보만 제공한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dataio import normalize

UNDER_MERGE_COLUMNS = ["name_norm", "place_id_a", "place_id_b", "gap_m", "n_clusters_of_name"]
ALIAS_COLUMNS = ["place_id_a", "place_id_b", "name_a", "name_b", "gap_m", "centroid_gap_m"]

# 후보 사전 선별의 안전 여유 — centroid round(6) 표기 오차(≤0.12m) 가드.
# 판정 임계가 아니다(후보를 넓게 잡는 하한 보정일 뿐 — 최종 판정은 정확 최근접 stop 거리).
_PREFILTER_GUARD_M = 1.0

_GAP_ROUND = 3   # CSV 표기 자릿수 (mm — 표기 규약, 임계값 아님)


def _coords_by_place(map_df: pd.DataFrame, stops: pd.DataFrame) -> dict:
    """place_id → (lat ndarray, lon ndarray) — 멤버 stop 좌표."""
    mp = map_df[["stop_id", "place_id"]].merge(
        stops[["stop_id", "lat", "lon"]], on="stop_id", how="left")
    return {pid: (g["lat"].to_numpy(float), g["lon"].to_numpy(float))
            for pid, g in mp.groupby("place_id")}


def min_gap_m(coords_a: tuple, coords_b: tuple) -> float:
    """두 place 간 최근접 stop 쌍 haversine 거리."""
    la, lo = coords_a
    lb, lob = coords_b
    d = normalize.haversine_m(la[:, None], lo[:, None], lb[None, :], lob[None, :])
    return float(np.min(d))


def diag_under_merge(places: pd.DataFrame, map_df: pd.DataFrame,
                     stops: pd.DataFrame, params: dict) -> pd.DataFrame:
    """같은 name_norm의 place 쌍 전량. gap_m = 최근접 stop 쌍 거리.

    diag_under_merge_max_m가 null이 아니면 gap_m <= 상한 행만.
    정렬: (name_norm, gap_m). 쌍 순서: place_id_a < place_id_b (사전순).
    """
    max_m = params.get("diag_under_merge_max_m")
    coords = _coords_by_place(map_df, stops)
    rows = []
    for name, g in places.groupby("name_norm", sort=True):
        ids = sorted(g["place_id"].tolist())
        k = len(ids)
        if k < 2:
            continue
        for i in range(k):
            for j in range(i + 1, k):
                gap = min_gap_m(coords[ids[i]], coords[ids[j]])
                if max_m is not None and gap > float(max_m):
                    continue
                rows.append((name, ids[i], ids[j], round(gap, _GAP_ROUND), k))
    df = pd.DataFrame(rows, columns=UNDER_MERGE_COLUMNS)
    return (df.sort_values(["name_norm", "gap_m"], kind="mergesort")
            .reset_index(drop=True))


def diag_alias(places: pd.DataFrame, map_df: pd.DataFrame,
               stops: pd.DataFrame, params: dict) -> pd.DataFrame:
    """다른 name_norm의 place 쌍 중 최근접 stop 쌍 거리 <= diag_alias_max_m.

    후보 사전 선별: haversine 삼각부등식에서 최근접 stop 거리 >= centroid 거리
    − span_a − span_b 이므로, centroid 거리 <= 임계 + span_a + span_b (+표기 오차 가드)
    쌍만 정확 계산한다. 정렬: (gap_m, place_id_a, place_id_b).
    """
    thr = float(params["diag_alias_max_m"])
    places_sorted = places.sort_values("place_id", kind="mergesort").reset_index(drop=True)
    n = len(places_sorted)
    if n < 2:
        return pd.DataFrame(columns=ALIAS_COLUMNS)
    coords = _coords_by_place(map_df, stops)
    ids = places_sorted["place_id"].to_numpy(object)
    names = places_sorted["name_norm"].to_numpy(object)
    lat = places_sorted["lat_centroid"].to_numpy(float)
    lon = places_sorted["lon_centroid"].to_numpy(float)
    span = places_sorted["span_m"].to_numpy(float)

    cd = np.atleast_2d(normalize.haversine_m(
        lat[:, None], lon[:, None], lat[None, :], lon[None, :]))
    bound = thr + span[:, None] + span[None, :] + _PREFILTER_GUARD_M
    cand = np.argwhere(np.triu(cd <= bound, k=1) & (names[:, None] != names[None, :]))

    rows = []
    for i, j in cand:
        gap = min_gap_m(coords[ids[i]], coords[ids[j]])
        if gap <= thr:
            rows.append((ids[i], ids[j], names[i], names[j],
                         round(gap, _GAP_ROUND), round(float(cd[i, j]), _GAP_ROUND)))
    df = pd.DataFrame(rows, columns=ALIAS_COLUMNS)
    return (df.sort_values(["gap_m", "place_id_a", "place_id_b"], kind="mergesort")
            .reset_index(drop=True))
