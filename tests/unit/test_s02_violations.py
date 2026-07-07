# s02 위반 주입 테스트 — "위반을 심은 데이터에서 검증기가 실제로 FAIL을 내는가"
# (verification.md §6.1, stage2_place_hub_spec.md §6.2의 s02 해당분)
import numpy as np
import pandas as pd
import pytest

from bts.io import ContractViolation, normalize
from bts.checks.contracts import s02_common
from bts.stages.s02_place import merge

_LAT0, _LON0 = 35.5, 129.3
_PARAMS = {"linkage_max_m": 150.0, "diag_alias_max_m": 30.0,
           "diag_under_merge_max_m": None, "span_warn_m": 300.0}


def _north(m):
    return _LAT0 + m / (normalize._EARTH_R_M * np.pi / 180.0)


def _stops(rows):
    return pd.DataFrame(rows, columns=["stop_id", "stop_name", "lat", "lon"])


def _frames(stops, overrides=None):
    ov = overrides if overrides is not None else pd.DataFrame(columns=merge.OVERRIDE_COLUMNS)
    return merge.build_frames(stops, _PARAMS, "before", overrides=ov, aliases={})


_BASE = _stops([
    ("P1", "가", _LAT0, _LON0),
    ("P2", "가", _north(50.0), _LON0),
    ("P3", "나", _north(400.0), _LON0),
    ("P4", "다", _north(800.0), _LON0),
])


# ── §6.2: cross-name 병합 주입 → C-S02-*-002 ────────────────────────────────
def test_cross_name_병합_주입은_C002_FAIL():
    f = _frames(_BASE)
    places, mp = f["places"], f["stop_place_map"].copy()
    target = places.loc[places["name_norm"] == "가", "place_id"].iloc[0]
    mp.loc[mp["stop_id"] == "P3", "place_id"] = target    # '나' stop을 '가' place로 직접 주입
    r = s02_common.c002_no_cross_name(places, mp, "before")
    assert r.status == "FAIL"
    # 대조군: 주입 없으면 PASS
    assert s02_common.c002_no_cross_name(places, f["stop_place_map"], "before").status == "PASS"


def test_n_names_gt1인데_override_플래그_없음도_C002_FAIL():
    f = _frames(_BASE)
    places = f["places"].copy()
    places.loc[0, "n_names"] = 2                          # 플래그 없는 다이름 place 주입
    r = s02_common.c002_no_cross_name(places, f["stop_place_map"], "before")
    assert r.status == "FAIL"


# ── §6.2: 같은 이름 200m 분리 쌍 → 병합 안 되고 diag_under_merge 등장 (진단 PC) ──
def test_같은_이름_200m_분리는_병합되지_않고_under_merge에_등장():
    stops = _stops([("P1", "가", _LAT0, _LON0), ("P2", "가", _north(200.0), _LON0)])
    f = _frames(stops)
    assert len(f["places"]) == 2                          # 병합 안 됨
    um = f["diag_under_merge"]
    assert len(um) == 1                                   # 진단 양성 대조군 — 부재 시 FAIL
    assert um.loc[0, "name_norm"] == "가"
    assert 195.0 < float(um.loc[0, "gap_m"]) < 205.0
    assert int(um.loc[0, "n_clusters_of_name"]) == 2


# ── §6.2: 다른 이름 20m 근접 쌍 → 병합 안 되고 diag_alias 등장 (진단 PC) ────
def test_다른_이름_20m_근접은_병합되지_않고_alias에_등장():
    stops = _stops([("P1", "가", _LAT0, _LON0), ("P2", "나", _north(20.0), _LON0)])
    f = _frames(stops)
    assert len(f["places"]) == 2                          # 자동 병합 금지
    al = f["diag_alias"]
    assert len(al) == 1
    assert {al.loc[0, "name_a"], al.loc[0, "name_b"]} == {"가", "나"}
    assert 15.0 < float(al.loc[0, "gap_m"]) < 25.0


# ── §6.2: stop_place_map 1행 삭제 → C-S02-*-001 (소실 검출) ─────────────────
def test_map_1행_삭제는_C001_FAIL():
    f = _frames(_BASE)
    mp = f["stop_place_map"]
    r = s02_common.c001_full_mapping(mp.iloc[1:], _BASE, "before",
                                     expected_total=len(_BASE))
    assert r.status == "FAIL"
    assert s02_common.c001_full_mapping(mp, _BASE, "before",
                                        expected_total=len(_BASE)).status == "PASS"


# ── §6.2: override가 존재하지 않는 place_id 참조 → 빌드 ContractViolation ───
def test_override_참조_불능은_빌드_즉사():
    ov = pd.DataFrame([("merge", "PB_deadbeef", "PB_00000000", "r", "s")],
                      columns=merge.OVERRIDE_COLUMNS)
    with pytest.raises(ContractViolation):
        _frames(_BASE, overrides=ov)


# ── 추가 위반 주입 — C-S02-*-003/004/005/006/008, P-S02-*-001 ────────────────
def test_place_id_변경은_C003_FAIL():
    f = _frames(_BASE)
    places, mp = f["places"].copy(), f["stop_place_map"].copy()
    old = places.loc[0, "place_id"]
    places.loc[0, "place_id"] = "PB_00000000"
    mp.loc[mp["place_id"] == old, "place_id"] = "PB_00000000"
    assert s02_common.c003_place_id_determinism(places, mp, "before").status == "FAIL"
    assert s02_common.c003_place_id_determinism(
        f["places"], f["stop_place_map"], "before").status == "PASS"


def test_임계_초과_stop을_같은_place로_직접_묶으면_C004_연결성_FAIL():
    stops = _stops([("P1", "가", _LAT0, _LON0), ("P2", "가", _north(200.0), _LON0)])
    f = _frames(stops)
    places, mp = f["places"], f["stop_place_map"].copy()
    keep = places["place_id"].iloc[0]
    mp["place_id"] = keep                                  # 200m 분리 쌍을 한 place로 주입
    assert s02_common.c004_cluster_validity(
        places.iloc[[0]], mp, stops, 150.0, "before").status == "FAIL"
    # 대조군: 정상 산출은 PASS (분리 정당성 > 150m 포함)
    assert s02_common.c004_cluster_validity(
        f["places"], f["stop_place_map"], stops, 150.0, "before").status == "PASS"


def test_같은_이름_place쌍이_임계_이내면_C004_분리정당성_FAIL():
    stops = _stops([("P1", "가", _LAT0, _LON0), ("P2", "가", _north(100.0), _LON0)])
    f = _frames(stops)                                     # 정상: 1 place로 병합됨
    # 위반 주입: 100m 쌍을 places 2개로 직접 분리
    pa = merge.make_place_id("before", "가", ["P1"])
    pb = merge.make_place_id("before", "가", ["P2"])
    places = pd.DataFrame({"place_id": [pa, pb], "name_norm": ["가", "가"],
                           "is_override_merged": [False, False]})
    mp = pd.DataFrame({"stop_id": ["P1", "P2"], "place_id": [pa, pb],
                       "name_norm": ["가", "가"]})
    assert s02_common.c004_cluster_validity(places, mp, stops, 150.0, "before").status == "FAIL"


def test_override_회계_불일치는_C005_FAIL():
    f = _frames(_BASE)
    ov = pd.DataFrame([("merge", "PB_a", "PB_b", "r", "s")], columns=merge.OVERRIDE_COLUMNS)
    r = s02_common.c005_override_accounting(f["override_applied"], ov, f["places"], "before")
    assert r.status == "FAIL"                              # applied 0행 != override 1행
    ok = s02_common.c005_override_accounting(
        f["override_applied"], pd.DataFrame(columns=merge.OVERRIDE_COLUMNS),
        f["places"], "before")
    assert ok.status == "PASS"                             # 빈 override → vacuous PASS


def test_이름당_stop_분포_회귀는_C006_FAIL():
    f = _frames(_BASE)                                     # 분포 {1:2, 2:1, 3+:0}
    mp = f["stop_place_map"]
    assert s02_common.c006_name_stop_distribution(
        mp, expected={"1": 2, "2": 1, "3+": 0}).status == "PASS"
    assert s02_common.c006_name_stop_distribution(
        mp, expected={"1": 3, "2": 1, "3+": 0}).status == "FAIL"


def test_n_stops_변경은_C008_accounting_FAIL():
    f = _frames(_BASE)
    places = f["places"].copy()
    places.loc[0, "n_stops"] = places.loc[0, "n_stops"] + 1
    r = s02_common.c008_output_integrity(places, f["stop_place_map"], _BASE, "before")
    assert r.status == "FAIL"
    assert s02_common.c008_output_integrity(
        f["places"], f["stop_place_map"], _BASE, "before").status == "PASS"


def test_centroid_변경은_C008_재계산_대조_FAIL():
    f = _frames(_BASE)
    places = f["places"].copy()
    places.loc[0, "lat_centroid"] = float(places.loc[0, "lat_centroid"]) + 0.001  # ≈111m 이동
    assert s02_common.c008_output_integrity(
        places, f["stop_place_map"], _BASE, "before").status == "FAIL"


def test_span_초과는_P001_WARN_FAIL_params_부재는_SKIP():
    f = _frames(_BASE)
    places = f["places"].copy()
    places.loc[0, "span_m"] = 500.0
    r = s02_common.p001_span(places, f["stop_place_map"], _BASE, _PARAMS, "before")
    assert r.status == "FAIL" and r.severity == "WARN"
    r2 = s02_common.p001_span(f["places"], f["stop_place_map"], _BASE, {}, "before")
    assert r2.status == "SKIP"


def test_진단_스키마와_교차_불변식_C007():
    f = _frames(_BASE)
    ok = s02_common.c007_diag_structure(f["diag_under_merge"], f["diag_alias"],
                                        f["places"], "before")
    assert ok.status == "PASS"
    assert s02_common.c007_diag_structure(None, f["diag_alias"],
                                          f["places"], "before").status == "FAIL"
    # 다른 이름 쌍이 under_merge에 끼어들면 FAIL
    bad = pd.DataFrame([{"name_norm": "가",
                         "place_id_a": f["places"]["place_id"].iloc[0],
                         "place_id_b": f["places"]["place_id"].iloc[2],
                         "gap_m": 10.0, "n_clusters_of_name": 2}])
    assert s02_common.c007_diag_structure(bad, f["diag_alias"],
                                          f["places"], "before").status == "FAIL"
