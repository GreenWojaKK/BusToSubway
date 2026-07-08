# s01_canonical/before 순수 함수 테스트.
# role별 base 처리, 재귀 해소, 판정표 조인 방향, 비결정 패턴의 evidence 선택,
# get_rules의 era 명시 요구를 각각 독립적으로 확인한다.
import numpy as np
import pandas as pd
import pytest

from dataio import ContractViolation
from s01_canonical import get_rules
from s01_canonical import before as s01b


# ── get_rules — era를 명시해야 규칙을 읽을 수 있다 ───────────────────────────
class TestGetRules:
    def test_before_로드(self):
        r = get_rules("before")
        assert r["era"] == "before"
        assert r["portability"] == "forbidden"

    def test_미지_era는_KeyError(self):
        with pytest.raises(KeyError):
            get_rules("전체")

    def test_support_토큰은_규칙_데이터에서(self):
        assert s01b._support_token(get_rules("before")) == "지원"


class TestSupportParent:
    def test_base명_규칙(self):
        # 지원 노선명에서 뒤쪽 지원 표기를 떼어 부모 route 이름을 얻는다.
        assert s01b.support_parent("13 지원2", "지원") == "13"
        assert s01b.support_parent("236 지원4", "지원") == "236"
        assert s01b.support_parent("924 지원2", "지원") == "924"


# ── build_patterns — 대표 정차열 선택 ───────────────────────────────────────
def _stop_times(rows):
    df = pd.DataFrame(rows, columns=["trip_id", "pattern_id", "route_name",
                                     "stop_id", "seq", "lineage"])
    df["seq"] = df["seq"].astype("int16")
    return df


def _ev(pid, stop_ids, route="r"):
    return {route: {"variants": [{"route_id": pid, "stop_ids": stop_ids}]}}


class TestBuildPatterns:
    def test_결정_패턴은_trip_시퀀스가_대표(self):
        st = _stop_times([("T1", "PA", "10(a)", "a", 1, "TAGO"),
                          ("T1", "PA", "10(a)", "b", 2, "TAGO"),
                          ("T2", "PA", "10(a)", "a", 1, "TAGO"),
                          ("T2", "PA", "10(a)", "b", 2, "TAGO")])
        core, ps = s01b.build_patterns(st, {})
        assert len(core) == 1 and not bool(core.at[0, "is_drt"])
        assert list(ps["stop_id"]) == ["a", "b"] and list(ps["seq"]) == [1, 2]
        assert core.at[0, "n_trips"] == 2 and core.at[0, "rep_len"] == 2

    def test_비결정_ACC0는_evidence_합집합_순회가_대표(self):
        st = _stop_times([("T1", "PB", "울주01", "a", 1, "ACC0"),
                          ("T1", "PB", "울주01", "b", 2, "ACC0"),
                          ("T2", "PB", "울주01", "b", 1, "ACC0"),
                          ("T2", "PB", "울주01", "c", 2, "ACC0")])
        core, ps = s01b.build_patterns(st, _ev("PB", ["a", "b", "c"]))
        assert bool(core.at[0, "is_drt"])
        assert list(ps["stop_id"]) == ["a", "b", "c"]   # 비결정 ACC0는 evidence 순서를 대표 정차열로 쓴다.

    def test_비결정_TAGO는_즉시_실패(self):
        st = _stop_times([("T1", "PX", "10(a)", "a", 1, "TAGO"),
                          ("T2", "PX", "10(a)", "b", 1, "TAGO")])
        with pytest.raises(ContractViolation):
            s01b.build_patterns(st, {})

    def test_비결정_evidence_distinct_불일치는_실패(self):
        st = _stop_times([("T1", "PB", "울주01", "a", 1, "ACC0"),
                          ("T2", "PB", "울주01", "b", 1, "ACC0")])
        with pytest.raises(ContractViolation):
            s01b.build_patterns(st, _ev("PB", ["a", "z"]))   # z ∉ schedule 합집합 {a,b}


# ── join_tags — 조인 방향 계약 ───────────────────────────────────────────────
def _pat_core(rows):
    return pd.DataFrame(rows, columns=["pattern_id", "pattern_key", "route_name",
                                       "lineage", "n_trips", "rep_len", "is_drt"])


def _vt(rows):
    return pd.DataFrame(rows, columns=["pattern_id", "route", "role", "n_stops",
                                       "frequency", "is_loop", "direction_group",
                                       "base_pattern_id_raw"])


class TestJoinTags:
    def test_미태깅_지원은_role_support로(self):
        pc = _pat_core([("P1", "k1", "10(a)", "TAGO", 3, 2, False),
                        ("P2", "k2", "13 지원2 (x)", "TAGO", 44, 5, False)])
        vt = _vt([("P1", "10", "main", 2, 3, True, "A:x", None)])
        j = s01b.join_tags(pc, vt, "지원")
        sup = j[j["pattern_id"] == "P2"].iloc[0]
        assert sup["role"] == "support" and sup["route"] == "13 지원2"
        assert sup["n_stops"] == 5 and sup["frequency"] == 44
        assert pd.isna(sup["is_loop"])   # 판정 입력에 없던 값은 새로 만들지 않고 NA로 둔다.

    def test_tags측_미매치는_실패(self):
        pc = _pat_core([("P1", "k1", "10(a)", "TAGO", 3, 2, False)])
        vt = _vt([("P1", "10", "main", 2, 3, True, "A:x", None),
                  ("P9", "10", "main", 2, 3, True, "B:x", None)])
        with pytest.raises(ContractViolation):
            s01b.join_tags(pc, vt, "지원")

    def test_비지원_미태깅은_실패(self):
        pc = _pat_core([("P1", "k1", "10(a)", "TAGO", 3, 2, False),
                        ("P2", "k2", "99(수상한 노선)", "TAGO", 1, 2, False)])
        vt = _vt([("P1", "10", "main", 2, 3, True, "A:x", None)])
        with pytest.raises(ContractViolation):
            s01b.join_tags(pc, vt, "지원")


# ── resolve_base_refs — base 처리는 role별로 달라야 한다 ────────────────────
def _tagged(rows):
    return pd.DataFrame(rows, columns=["pattern_id", "role", "base_pattern_id_raw"])


class TestResolveBaseRefs:
    def test_main_자기참조만_NaN_circular는_보존(self):
        df = _tagged([("M1", "main", "M1"),        # 자기참조 → NaN (표기 변형)
                      ("M2", "main", None),
                      ("C1", "circular", "C1"),    # 자기참조 → 'base 있음' 보존
                      ("C2", "circular", None)])
        out = s01b.resolve_base_refs(df, max_depth=4).set_index("pattern_id")
        assert pd.isna(out.at["M1", "base_ref"])
        assert out.at["C1", "base_ref"] == "C1"            # circular 자기참조는 그대로 유지한다.
        assert out.at["C1", "base_ref_resolved"] == "C1"   # circular는 정지 role

    def test_재귀_해소는_canonical_조상까지(self):
        df = _tagged([("M1", "main", None),
                      ("S0", "short_turn", "M1"),
                      ("S1", "short_turn", "S0"),      # 체인 (147형)
                      ("B0", "branch", "M1"),
                      ("D0", "detour", "B0")])         # detour→branch
        out = s01b.resolve_base_refs(df, max_depth=4).set_index("pattern_id")
        assert out.at["S1", "base_ref_resolved"] == "M1"
        assert out.at["D0", "base_ref_resolved"] == "M1"
        assert out.at["S0", "base_ref_resolved"] == "M1"   # 직접 참조도 같은 방식으로 해소한다.

    def test_depth_가드(self):
        df = _tagged([("M1", "main", None),
                      ("S0", "short_turn", "M1"), ("S1", "short_turn", "S0"),
                      ("S2", "short_turn", "S1"), ("S3", "short_turn", "S2"),
                      ("S4", "short_turn", "S3")])
        with pytest.raises(ContractViolation):
            s01b.resolve_base_refs(df, max_depth=2)

    def test_dangling_참조는_실패(self):
        df = _tagged([("S0", "short_turn", "GHOST")])
        with pytest.raises(ContractViolation):
            s01b.resolve_base_refs(df, max_depth=4)


# ── build_disposition — 처리 결과는 role_scope와 params로만 결정된다 ─────────
class TestBuildDisposition:
    def test_role_scope와_처리결과(self):
        df = _tagged([("M1", "main", None), ("C1", "circular", "C1"),
                      ("C2", "circular", None), ("S1", "short_turn", "M1"),
                      ("D1", "detour", "M1"), ("A1", "anomaly", None)])
        df = s01b.resolve_base_refs(df, max_depth=4)
        out = s01b.build_disposition(
            df, ["main", "circular_baseless", "short_turn", "branch"],
            ["main", "circular_baseless"]).set_index("pattern_id")
        assert out.at["C1", "role_scope"] == "circular_with_base"
        assert out.at["C1", "disposition"] == "excl_circular_with_base"
        assert out.at["C2", "role_scope"] == "circular_baseless"
        assert bool(out.at["C2", "in_canonical"]) and bool(out.at["C2", "in_backbone"])
        assert bool(out.at["S1", "in_canonical"]) and not bool(out.at["S1", "in_backbone"])
        assert out.at["D1", "disposition"] == "excl_detour"
        assert out.at["A1", "disposition"] == "excl_anomaly"


# ── detect_duplicates — 노선 간 동일 정차열 명시 ─────────────────────────────
class TestDetectDuplicates:
    def test_route가_다른_충돌만(self):
        pat = pd.DataFrame({"pattern_id": ["P1", "P2", "P3", "P4"],
                            "pattern_key": ["k1", "k1", "k2", "k2"],
                            "route": ["22", "977", "10", "10"]})
        d = s01b.detect_duplicates(pat)
        assert len(d) == 1                       # 같은 route 안의 동일 정차열은 중복 비교에서 제외한다.
        assert d.at[0, "routes"] == "22|977" and d.at[0, "policy"] == "keep_flag"


# ── classify_routes — expect_count 자가 검증 ─────────────────────────────────
_RULES = {"era": "test",
          "rules": [{"class": "express", "rule": r"fullmatch:\d{4}", "expect_bases": 1},
                    {"class": "support", "rule": "contains:지원", "expect_bases": 1,
                     "scope_out": True},
                    {"class": "general", "rule": "fallback", "expect_bases": 2}],
          "accounting": {"regular_bases": 3, "catalog_bases": 4}}


def _catalog_base():
    return pd.DataFrame({"route": ["10", "5001", "13 지원2", "20"],
                         "has_schedule": [True, True, True, False],
                         "lineage": ["TAGO"] * 4,
                         "n_patterns": np.array([1, 1, 1, 0], dtype="int32"),
                         "n_trips": np.array([5, 5, 1, 0], dtype="int32")})


class TestClassifyRoutes:
    def test_분류와_rule_id(self):
        out = s01b.classify_routes(_catalog_base(), _RULES).set_index("route")
        assert out.at["5001", "route_class"] == "express"
        assert out.at["13 지원2", "route_class"] == "support"
        assert out.at["10", "route_class"] == "general"
        assert out.at["10", "rule_id"] == "test:general"

    def test_expect_count_위반은_실패(self):
        rules = {**_RULES, "rules": [dict(r) for r in _RULES["rules"]]}
        rules["rules"][0]["expect_bases"] = 2    # 규칙 파일의 기대 행수를 일부러 깨뜨린다.
        with pytest.raises(ContractViolation):
            s01b.classify_routes(_catalog_base(), rules)

    def test_accounting_위반은_실패(self):
        rules = dict(_RULES)
        rules["accounting"] = {"regular_bases": 3, "catalog_bases": 5}
        with pytest.raises(ContractViolation):
            s01b.classify_routes(_catalog_base(), rules)
