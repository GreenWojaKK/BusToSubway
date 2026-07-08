# s02_place cluster_same_name 단위 테스트 (stage2_place_hub_spec.md §6.1 test_place_cluster)
# — ≤ 경계 고정 / single-linkage 체인 / cross-name 병합 경로 부재 / alias 치환
import numpy as np
import pandas as pd

from dataio import normalize
from s02_place import merge

_LAT0, _LON0 = 35.5, 129.3


def _north(m: float) -> float:
    """위도 방향으로 m 미터 이동한 위도 (haversine에서 순수 위도 이동 = R·Δφ)."""
    return _LAT0 + m / (normalize._EARTH_R_M * np.pi / 180.0)


def _stops(rows):
    return pd.DataFrame(rows, columns=["stop_id", "stop_name", "lat", "lon"])


def test_경계_포함_임계와_같은_거리는_병합_초과는_분리():
    # 부동소수 경계를 배제하기 위해 '측정된 거리 그 자체'를 임계로 사용한다 (≤ 경계 고정)
    p = _stops([("P1", "가", _LAT0, _LON0), ("P2", "가", _north(150.0), _LON0)])
    d = float(normalize.haversine_m(_LAT0, _LON0, _north(150.0), _LON0))
    same = merge.cluster_same_name(p, linkage_max_m=d)
    assert same["P1"] == same["P2"]                       # dist == 임계 → 병합 (≤)
    apart = merge.cluster_same_name(p, linkage_max_m=float(np.nextafter(d, 0.0)))
    assert apart["P1"] != apart["P2"]                     # dist > 임계 → 분리


def test_150m_이내_병합_150m_초과_분리():
    near = _stops([("P1", "가", _LAT0, _LON0), ("P2", "가", _north(149.0), _LON0)])
    far = _stops([("P1", "가", _LAT0, _LON0), ("P2", "가", _north(151.0), _LON0)])
    assert merge.cluster_same_name(near, 150.0)["P1"] == merge.cluster_same_name(near, 150.0)["P2"]
    labels = merge.cluster_same_name(far, 150.0)
    assert labels["P1"] != labels["P2"]


def test_single_linkage_체인_140_140_끝점_280은_한_클러스터():
    p = _stops([("P1", "가", _LAT0, _LON0),
                ("P2", "가", _north(140.0), _LON0),
                ("P3", "가", _north(280.0), _LON0)])
    labels = merge.cluster_same_name(p, 150.0)
    assert labels["P1"] == labels["P2"] == labels["P3"]   # 끝점 280m여도 연쇄 연결


def test_다른_이름은_1m여도_분리_cross_name_경로_부재():
    p = _stops([("P1", "가", _LAT0, _LON0), ("P2", "나", _north(1.0), _LON0)])
    labels = merge.cluster_same_name(p, 150.0)
    assert labels["P1"] != labels["P2"]
    assert labels["P1"][0] == "가" and labels["P2"][0] == "나"   # 라벨에 name_norm 보존


def test_alias는_정규화_후_치환되어_같은_블록이_된다():
    p = _stops([("P1", "가", _LAT0, _LON0), ("P2", " 가옛 ", _north(10.0), _LON0)])
    meta = {}
    labels = merge.cluster_same_name(p, 150.0, aliases={"가옛": "가"}, meta=meta)
    assert labels["P1"] == labels["P2"]                   # strip 후 alias 적용 → 동일 블록
    assert meta["n_alias_applied"] == 1


def test_클러스터_라벨은_컴포넌트_최소_stop_id():
    p = _stops([("P9", "가", _LAT0, _LON0), ("P1", "가", _north(10.0), _LON0)])
    labels = merge.cluster_same_name(p, 150.0)
    assert labels["P9"] == ("가", "P1")
