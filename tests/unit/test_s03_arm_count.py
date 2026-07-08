# s03 arm(A) 단위 테스트 — ADR-009 방위각 원형 gap 군집 (stage2_place_hub_spec.md §6.1)
import numpy as np
import pandas as pd

from dataio import normalize
from s03_hub import metrics as m

_LAT0, _LON0 = 35.5, 129.3
_THETA = 45.0
_DIST_M = 1000.0


def _offset(bearing_deg: float, dist_m: float = _DIST_M) -> tuple[float, float]:
    """중심에서 초기 방위각 bearing 방향으로 dist_m 떨어진 좌표(소거리 근사)."""
    br = np.radians(bearing_deg)
    dlat = np.degrees(dist_m * np.cos(br) / normalize._EARTH_R_M)
    dlon = np.degrees(dist_m * np.sin(br) / (normalize._EARTH_R_M * np.cos(np.radians(_LAT0))))
    return _LAT0 + dlat, _LON0 + dlon


def _fixture(bearings: list[float]):
    """중심 P0 + 방위각별 이웃 place — (edges, places)."""
    rows = [("P0", "중심", _LAT0, _LON0)]
    edges = []
    for i, b in enumerate(bearings, start=1):
        lat, lon = _offset(b)
        rows.append((f"P{i}", f"이웃{i}", lat, lon))
        a, c = sorted(["P0", f"P{i}"])
        edges.append((a, c))
    places = pd.DataFrame(rows, columns=["place_id", "name_norm", "lat_centroid", "lon_centroid"])
    edf = pd.DataFrame(edges, columns=["place_a", "place_b"])
    return edf, places


def _arm_of_center(bearings):
    edges, places = _fixture(bearings)
    return int(m.compute_arms(edges, places, _THETA).loc["P0"])


def test_십자_4방향_theta45는_4팔():
    assert _arm_of_center([0.0, 90.0, 180.0, 270.0]) == 4


def test_회랑_2방향은_2팔():
    assert _arm_of_center([0.0, 180.0]) == 2


def test_인접_2방향_30도는_1팔():
    assert _arm_of_center([0.0, 30.0]) == 1


def test_D1은_A1():
    assert _arm_of_center([77.0]) == 1


def test_D0은_A0():
    edges, places = _fixture([0.0])
    lonely = places[places["place_id"] == "P0"].copy()
    a = m.compute_arms(edges.iloc[0:0], lonely, _THETA)
    assert int(a.loc["P0"]) == 0


def test_wrap_350과_10도는_1팔():
    assert _arm_of_center([350.0, 10.0]) == 1


def test_항등_1_le_A_le_D():
    edges, places = _fixture([0.0, 10.0, 20.0, 200.0, 210.0])
    a = m.compute_arms(edges, places, _THETA)
    d = m.compute_degree(edges, places)
    assert ((a[d >= 1] >= 1) & (a[d >= 1] <= d[d >= 1])).all()
