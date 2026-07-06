# normalize 단위 테스트 — base 추출 + 공인 조인기 (verification.md §6.2)
import numpy as np
import pandas as pd
import pytest

from bts.io import normalize as nz


class TestBaseRouteName:
    def test_방면_괄호_제거(self):
        assert nz.base_route_name("837(태화강역방면)") == "837"

    def test_중첩_괄호_greedy(self):
        assert nz.base_route_name("924 지원2 (문수초지원(오후))") == "924 지원2"

    def test_괄호_없는_이름_불변(self):
        assert nz.base_route_name("울주01") == "울주01"

    def test_숫자_절단_파싱_부재(self):
        # base 추출은 route_name 정규식만 — 숫자 코어 절단이 아님을 확인
        assert nz.base_route_name("50(내고산 방면)") == "50"
        assert nz.base_route_name("13 지원2") == "13 지원2"   # 절단이면 '13'이 됐을 것


def _stops(rows):
    return pd.DataFrame(rows, columns=["stop_id", "stop_name", "lat", "lon"])


# 위도 35.5에서 약 1m ≈ 0.000009도
DEG_1M = 0.000009


class TestResolver:
    def test_alias_양우내안에_정확히_1건_적용(self):
        # [PC] 양성 대조군의 위치 교정본: stop 우주에는 '양우내안애'만 존재 (design.md §5 s02 주의)
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
        # 반올림 자릿수가 달라도(문자열 불일치) 거리 기반으로 해소됨 — exact 조인 금지의 검증
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
