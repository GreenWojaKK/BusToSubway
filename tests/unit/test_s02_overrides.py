# apply_overrides 의미론 테스트 (stage2_place_hub_spec.md §4.2)
# — 빈 override 항등(완결 계약) / merge / 연쇄 참조 / 참조 불능·미지 action ContractViolation
import numpy as np
import pandas as pd
import pytest

from bts.io import ContractViolation, normalize
from bts.stages.s02_place import merge

_LAT0, _LON0 = 35.5, 129.3
_PARAMS = {"linkage_max_m": 150.0, "diag_alias_max_m": 30.0, "diag_under_merge_max_m": None}


def _north(m):
    return _LAT0 + m / (normalize._EARTH_R_M * np.pi / 180.0)


def _stops():
    # X 이름 2클러스터(500m 분리) + Y 이름 1클러스터
    return pd.DataFrame([
        ("P1", "X", _LAT0, _LON0),
        ("P2", "X", _north(500.0), _LON0),
        ("P3", "Y", _north(1000.0), _LON0),
    ], columns=["stop_id", "stop_name", "lat", "lon"])


def _pre():
    stops = _stops()
    labels = merge.cluster_same_name(stops, _PARAMS["linkage_max_m"])
    members = {}
    for pid, lab in labels.items():
        members.setdefault(lab, []).append(pid)
    skel, map_rows = [], []
    for (name, _), mem in sorted(members.items()):
        place_id = merge.make_place_id("before", name, mem)
        skel.append((place_id, name, False))
        map_rows.extend((m, place_id, name) for m in mem)
    places_pre = pd.DataFrame(skel, columns=["place_id", "name_norm", "is_override_merged"])
    map_pre = pd.DataFrame(map_rows, columns=["stop_id", "place_id", "name_norm"])
    return stops, places_pre, map_pre


def _ov(rows):
    return pd.DataFrame(rows, columns=merge.OVERRIDE_COLUMNS)


def test_빈_override는_항등_그리고_applied는_헤더만():
    stops, places_pre, map_pre = _pre()
    skel, map_df, applied = merge.apply_overrides(places_pre, map_pre, _ov([]), "before")
    assert sorted(skel["place_id"]) == sorted(places_pre["place_id"])
    assert not skel["is_override_merged"].any()
    assert len(applied) == 0 and list(applied.columns) == merge.APPLIED_COLUMNS
    assert sorted(map_df["stop_id"]) == sorted(map_pre["stop_id"])


def test_merge는_place_b를_place_a로_흡수하고_id를_재계산():
    stops, places_pre, map_pre = _pre()
    a = merge.make_place_id("before", "X", ["P1"])
    b = merge.make_place_id("before", "X", ["P2"])
    skel, map_df, applied = merge.apply_overrides(
        places_pre, map_pre, _ov([("merge", a, b, "동일 역 판정", "test")]), "before")
    new_id = merge.make_place_id("before", "X", ["P1", "P2"])
    assert new_id in set(skel["place_id"]) and b not in set(skel["place_id"])
    row = skel.set_index("place_id").loc[new_id]
    assert row["name_norm"] == "X" and bool(row["is_override_merged"])
    assert set(map_df.loc[map_df["place_id"] == new_id, "stop_id"]) == {"P1", "P2"}
    assert len(applied) == 1
    assert applied.loc[0, "result_place_id"] == new_id
    assert applied.loc[0, "n_stops_merged"] == 2
    # 병합 place의 n_names 집계 (같은 이름 병합 → 1, is_override_merged=True)
    places, _ = merge.assemble_places(skel, map_df, stops)
    got = places.set_index("place_id").loc[new_id]
    assert int(got["n_names"]) == 1 and bool(got["is_override_merged"])


def test_연쇄_병합은_앞_행이_만든_새_id를_참조해야_한다():
    # cross-name merge(Y←X)는 결과 id가 양쪽 원본과 달라진다(min 멤버가 place_b 소속) —
    # 연쇄 행은 그 새 id를 참조해야 하고, 소멸한 원본 id 참조는 거부된다 (spec §4.2)
    stops, places_pre, map_pre = _pre()
    a = merge.make_place_id("before", "X", ["P1"])
    b = merge.make_place_id("before", "X", ["P2"])
    c = merge.make_place_id("before", "Y", ["P3"])
    r1 = merge.make_place_id("before", "Y", ["P1", "P3"])
    assert r1 not in (a, c)                               # 진짜 새 id인 시나리오
    skel, map_df, applied = merge.apply_overrides(
        places_pre, map_pre,
        _ov([("merge", c, a, "r1", "test"), ("merge", r1, b, "r2", "test")]), "before")
    final = merge.make_place_id("before", "Y", ["P1", "P2", "P3"])
    assert list(skel["place_id"]) == [final]
    places, _ = merge.assemble_places(skel, map_df, stops)
    assert int(places.loc[0, "n_names"]) == 2             # X·Y 두 이름 병합
    assert places.loc[0, "name_norm"] == "Y"              # 대표 이름 = place_a(=c)의 name_norm
    assert list(applied["result_place_id"]) == [r1, final]
    # 낡은 id(앞 행에서 소멸한 a)를 참조하면 거부
    with pytest.raises(ContractViolation):
        merge.apply_overrides(
            places_pre, map_pre,
            _ov([("merge", c, a, "r1", "test"), ("merge", a, b, "r2", "test")]), "before")


def test_같은_이름_병합의_결과_id는_min_멤버_보유_쪽과_같다():
    # 콘텐츠 파생 id의 귀결: sha1(name|min) — 같은 이름 X의 두 클러스터를 병합하면
    # min stop을 가진 클러스터의 id가 결과 id로 유지된다 (참조 안정성의 문서화)
    stops, places_pre, map_pre = _pre()
    a = merge.make_place_id("before", "X", ["P1"])
    b = merge.make_place_id("before", "X", ["P2"])
    skel, _, applied = merge.apply_overrides(
        places_pre, map_pre, _ov([("merge", a, b, "r", "s")]), "before")
    assert applied.loc[0, "result_place_id"] == a         # min=P1 → id 불변
    assert bool(skel.set_index("place_id").loc[a, "is_override_merged"])


def test_참조_불능_place_id는_ContractViolation():
    stops, places_pre, map_pre = _pre()
    with pytest.raises(ContractViolation):
        merge.apply_overrides(places_pre, map_pre,
                              _ov([("merge", "PB_deadbeef", "PB_00000000", "r", "s")]), "before")


def test_미지_action과_자기_병합은_ContractViolation():
    stops, places_pre, map_pre = _pre()
    a = merge.make_place_id("before", "X", ["P1"])
    b = merge.make_place_id("before", "X", ["P2"])
    with pytest.raises(ContractViolation):
        merge.apply_overrides(places_pre, map_pre, _ov([("split", a, b, "r", "s")]), "before")
    with pytest.raises(ContractViolation):
        merge.apply_overrides(places_pre, map_pre, _ov([("merge", a, a, "r", "s")]), "before")


def test_build_frames_빈_override_경로_완결():
    stops = _stops()
    frames = merge.build_frames(stops, _PARAMS, "before", overrides=_ov([]), aliases={})
    assert len(frames["places"]) == 3                     # X 2클러스터 + Y 1
    assert len(frames["stop_place_map"]) == 3             # stop 전량 전사
    assert len(frames["override_applied"]) == 0
    assert list(frames["override_applied"].columns) == merge.APPLIED_COLUMNS
