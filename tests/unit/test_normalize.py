# normalize 단위 테스트 — route 이름 정리와 name→stop 해소 경로를 검증한다.
import numpy as np
import pandas as pd
import pytest

from dataio import normalize as nz


class TestBaseRouteName:
    def test_방면_괄호_제거(self):
        assert nz.base_route_name("837(태화강역방면)") == "837"

    def test_중첩_괄호_greedy(self):
        assert nz.base_route_name("924 지원2 (문수초지원(오후))") == "924 지원2"

    def test_괄호_없는_이름_불변(self):
        assert nz.base_route_name("울주01") == "울주01"

    def test_숫자_절단_파싱_부재(self):
        # base는 괄호 접미사만 제거해 만든다. 숫자로 된 본문은 잘라내지 않는다.
        assert nz.base_route_name("50(내고산 방면)") == "50"
        assert nz.base_route_name("13 지원2") == "13 지원2"   # 절단이면 '13'이 됐을 것


def _stops(rows):
    return pd.DataFrame(rows, columns=["stop_id", "stop_name", "lat", "lon"])


# 위도 35.5 부근에서 1m를 대략적인 위도 차이로 표현한다.
DEG_1M = 0.000009


class TestResolver:
    def test_alias_양우내안에_정확히_1건_적용(self):
        # alias를 적용한 뒤 실제 stop 표기 하나로 해소되는지 확인한다.
        stops = _stops([("BS_1", "양우내안애", 35.5, 129.3)])
        df = pd.DataFrame({"name": ["양우내안에"], "lat": [35.5 + DEG_1M * 0.9], "lon": [129.3]})
        resolved, failures, n_alias = nz.resolve_stops_by_name(
            df, stops, "name", "lat", "lon",
            aliases={"양우내안에": "양우내안애"})
        assert n_alias == 1
        assert failures.empty
        assert resolved.iloc[0] == "BS_1"

    def test_같은_이름_복수_stop_최근접_선택(self):
        stops = _stops([("BS_A", "공업탑", 35.5, 129.3),
                        ("BS_B", "공업탑", 35.5 + DEG_1M * 50, 129.3)])
        df = pd.DataFrame({"name": ["공업탑"], "lat": [35.5 + DEG_1M * 50.5], "lon": [129.3]})
        resolved, failures, _ = nz.resolve_stops_by_name(
            df, stops, "name", "lat", "lon", max_dist_m=10.0, aliases={})
        assert failures.empty
        assert resolved.iloc[0] == "BS_B"

    def test_1m_초과는_실패_행_반환(self):
        stops = _stops([("BS_A", "공업탑", 35.5, 129.3)])
        df = pd.DataFrame({"name": ["공업탑"], "lat": [35.5 + DEG_1M * 3], "lon": [129.3]})
        resolved, failures, _ = nz.resolve_stops_by_name(
            df, stops, "name", "lat", "lon", max_dist_m=1.0, aliases={})
        assert len(failures) == 1
        assert failures["reason"].iloc[0] == "too_far"
        assert pd.isna(resolved.iloc[0])

    def test_이름_불일치_실패(self):
        stops = _stops([("BS_A", "공업탑", 35.5, 129.3)])
        df = pd.DataFrame({"name": ["없는이름"], "lat": [35.5], "lon": [129.3]})
        _, failures, _ = nz.resolve_stops_by_name(
            df, stops, "name", "lat", "lon", max_dist_m=1.0, aliases={})
        assert failures["reason"].iloc[0] == "no_name_match"

    def test_좌표_문자열_조인_미사용(self):
        # 좌표 문자열이 정확히 같지 않아도, 같은 이름 안에서는 거리로 가장 가까운 stop을 고른다.
        stops = _stops([("BS_A", "공업탑", 35.55000, 129.30000)])
        df = pd.DataFrame({"name": ["공업탑"], "lat": [35.5500000001], "lon": [129.3]})
        resolved, failures, _ = nz.resolve_stops_by_name(
            df, stops, "name", "lat", "lon", max_dist_m=1.0, aliases={})
        assert failures.empty and resolved.iloc[0] == "BS_A"


class TestHaversine:
    def test_영거리(self):
        assert nz.haversine_m(35.5, 129.3, 35.5, 129.3) == pytest.approx(0.0)

    def test_1m_근사(self):
        d = nz.haversine_m(35.5, 129.3, 35.5 + DEG_1M, 129.3)
        assert d == pytest.approx(1.0, rel=0.05)

    def test_벡터화(self):
        d = nz.haversine_m(35.5, 129.3, np.array([35.5, 35.6]), np.array([129.3, 129.3]))
        assert d.shape == (2,)
