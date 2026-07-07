# s03 위반 주입 테스트 — "위반을 심은 데이터에서 검증기가 실제로 FAIL을 내는가"
# (verification.md §6.1, stage2_place_hub_spec.md §6.2의 s03 해당분)
import numpy as np
import pandas as pd
import pytest

from bts.checks import lint
from bts.checks.contracts import s03_before as chk
from bts.io import ContractViolation, normalize
from bts.stages.s03_hub import metrics as m
from bts.stages.s03_hub import qualify as q

_LAT0, _LON0 = 35.5, 129.3

_PARAMS = {
    "universe": "general_before",
    "arm_theta_deg": 45.0,
    "lifetime": {"mask_mode": "masked_global", "dominance": "weak", "k_max": 10},
    "lstar_gate_min_degree": 3,
    "thresholds": {"crossing_min_degree": 3, "crossing_min_arms": 3,
                   "terminal_min_degree": 5, "terminal_max_arms": 2,
                   "terminal_min_lstar": 5},
    "lspace_edge_max_m": 15000,
    "sensitivity_grid": {"mask_mode": ["masked_global", "subgraph"],
                         "dominance": ["weak", "strict"],
                         "k_max": [5, 10, 15],
                         "arm_theta_deg": [30.0, 45.0, 60.0]},
}
_UNIVERSE = {"R1", "R2"}


def _north(mm):
    return _LAT0 + mm / (normalize._EARTH_R_M * np.pi / 180.0)


@pytest.fixture()
def fx():
    """합성 정상 파이프라인 산출 일체 — 각 테스트가 1군데씩 바꾼다."""
    places = pd.DataFrame({
        "place_id": ["PB_1", "PB_2", "PB_3", "PB_4"],
        "name_norm": ["가", "나", "다", "라"],
        "lat_centroid": [_LAT0, _north(500), _north(1000), _north(9000)],
        "lon_centroid": [_LON0] * 4,
    })
    edges = pd.DataFrame({
        "place_a": ["PB_1", "PB_2"], "place_b": ["PB_2", "PB_3"],
        "n_patterns": np.array([1, 1], dtype="int16"),
        "n_routes": np.array([1, 1], dtype="int16"),
        "routes": ["R1", "R1|R2"],
        "gap_m": [500.0, 500.0],
    })
    d = m.compute_degree(edges, places)
    a = m.compute_arms(edges, places, _PARAMS["arm_theta_deg"])
    tables = m.dominance_tables(m._adjacency(edges), d, 15)
    l = m.compute_lifetime(edges, places, d, "masked_global", "weak", 10, tables=tables)
    metrics = m.assemble_metrics(places, d, a, l, 3)
    qual = q.apply_hub_overrides(
        q.qualify(metrics, _PARAMS["thresholds"]),
        pd.DataFrame(columns=q.OVERRIDE_COLUMNS))
    sens = m.build_sensitivity(places, edges, d, tables, _PARAMS)
    gap = pd.DataFrame([("PB_4", "라", "no_pattern", "")], columns=m.GAP_COLUMNS)
    return {"places": places, "edges": edges, "metrics": metrics,
            "qual": qual, "sens": sens, "gap": gap}


# ── 대조군: 변경 없으면 전부 PASS ────────────────────────────────────────────
def test_정상_fixture는_전_체크_PASS(fx):
    assert chk.c001_full_metrics(fx["metrics"], fx["places"]).status == "PASS"
    assert chk.c002_lstar_identity(fx["metrics"], 3).status == "PASS"
    assert chk.c003_qualification_recompute(fx["qual"], fx["metrics"],
                                            _PARAMS["thresholds"]).status == "PASS"
    assert chk.c004_arm_leq_degree(fx["metrics"]).status == "PASS"
    assert chk.c005_edge_integrity(fx["edges"], fx["places"], _UNIVERSE).status == "PASS"
    assert chk.c006_domain_identities(fx["metrics"], 10).status == "PASS"
    assert chk.c007_sensitivity(fx["sens"], fx["qual"], _PARAMS).status == "PASS"
    assert chk.c008_override_integrity(
        fx["qual"], pd.DataFrame(columns=q.OVERRIDE_COLUMNS), fx["places"]).status == "PASS"
    assert chk.c009_class_accounting(fx["qual"], fx["places"],
                                     _PARAMS["thresholds"]).status == "PASS"
    assert chk.c010_gap_accounting(fx["gap"], fx["metrics"]).status == "PASS"
    assert chk.p001_edge_gap(fx["edges"], _PARAMS).status == "PASS"


# ── §6.2: place_metrics에서 D==0 행 삭제 → C-S03-B-001 ──────────────────────
def test_D0_행_삭제는_C001_FAIL(fx):
    bad = fx["metrics"][fx["metrics"]["D"].astype(int) != 0]
    assert chk.c001_full_metrics(bad, fx["places"]).status == "FAIL"


# ── §6.2: L* 조건을 적용하지 않은 값 주입(D=2, L=4, L*=4) → C-S03-B-002 ───
def test_Lstar_without_condition_fails_C002(fx):
    bad = fx["metrics"].copy()
    i = bad.index[bad["place_id"] == "PB_2"][0]
    bad.loc[i, ["D", "L", "L_star"]] = [2, 4, 4]
    assert chk.c002_lstar_identity(bad, 3).status == "FAIL"


# ── §6.2: qualification에 임계 무관 판정 1행 주입 → C-S03-B-003 ──────────────
def test_임계_무관_판정_주입은_C003_FAIL(fx):
    bad = fx["qual"].copy()
    bad.loc[0, "hub_class"] = "CROSSING"          # rule은 NONE인데 판정만 조작
    assert chk.c003_qualification_recompute(bad, fx["metrics"],
                                            _PARAMS["thresholds"]).status == "FAIL"
    bad2 = fx["qual"].copy()
    bad2.loc[0, "is_crossing"] = True             # 순수 함수 결과 위조
    assert chk.c003_qualification_recompute(bad2, fx["metrics"],
                                            _PARAMS["thresholds"]).status == "FAIL"


# ── §6.2: A=D+1 행 주입 → C-S03-B-004 ────────────────────────────────────────
def test_A_gt_D_주입은_C004_FAIL(fx):
    bad = fx["metrics"].copy()
    i = bad.index[bad["place_id"] == "PB_1"][0]
    bad.loc[i, "A"] = int(bad.loc[i, "D"]) + 1
    assert chk.c004_arm_leq_degree(bad).status == "FAIL"


# ── §6.2: 엣지에 universe 밖 route(express) 1행 주입 → C-S03-B-005 ───────────
def test_universe_밖_route_주입은_C005_FAIL(fx):
    bad = pd.concat([fx["edges"], pd.DataFrame([{
        "place_a": "PB_1", "place_b": "PB_3", "n_patterns": 1, "n_routes": 1,
        "routes": "EXPRESS1421", "gap_m": 1000.0}])], ignore_index=True)
    assert chk.c005_edge_integrity(bad, fx["places"], _UNIVERSE).status == "FAIL"


def test_self_loop과_역순_쌍도_C005_FAIL(fx):
    loop = pd.concat([fx["edges"], pd.DataFrame([{
        "place_a": "PB_1", "place_b": "PB_1", "n_patterns": 1, "n_routes": 1,
        "routes": "R1", "gap_m": 0.0}])], ignore_index=True)
    assert chk.c005_edge_integrity(loop, fx["places"], _UNIVERSE).status == "FAIL"
    rev = fx["edges"].copy()
    rev.loc[0, ["place_a", "place_b"]] = ["PB_2", "PB_1"]
    assert chk.c005_edge_integrity(rev, fx["places"], _UNIVERSE).status == "FAIL"


# ── §6.2: sensitivity에서 is_adopted 행 삭제 → C-S03-B-007 ───────────────────
def test_is_adopted_행_삭제는_C007_FAIL(fx):
    bad = fx["sens"][~fx["sens"]["is_adopted"].astype(bool)].reset_index(drop=True)
    assert chk.c007_sensitivity(bad, fx["qual"], _PARAMS).status == "FAIL"


def test_채택_행_수치_조작도_C007_FAIL(fx):
    bad = fx["sens"].copy()
    i = bad.index[bad["is_adopted"].astype(bool)][0]
    bad.loc[i, "n_hub_qualified"] = int(bad.loc[i, "n_hub_qualified"]) + 1
    assert chk.c007_sensitivity(bad, fx["qual"], _PARAMS).status == "FAIL"


# ── §6.2: diag_lspace_gap에서 1행 누락 → C-S03-B-010 (accounting) ────────────
def test_gap_1행_누락은_C010_FAIL(fx):
    assert chk.c010_gap_accounting(fx["gap"].iloc[0:0], fx["metrics"]).status == "FAIL"


# ── 도메인 항등(C-S03-B-006)·엣지 gap(P-S03-B-001) 위반 주입 ─────────────────
def test_in_lspace_불일치_주입은_C006_FAIL(fx):
    bad = fx["metrics"].copy()
    i = bad.index[bad["place_id"] == "PB_4"][0]
    bad.loc[i, "in_lspace"] = True                # D=0인데 in_lspace=True
    assert chk.c006_domain_identities(bad, 10).status == "FAIL"


def test_임계_초과_gap_엣지는_P001_FAIL(fx):
    bad = fx["edges"].copy()
    bad.loc[0, "gap_m"] = float(_PARAMS["lspace_edge_max_m"]) + 1.0
    r = chk.p001_edge_gap(bad, _PARAMS)
    assert r.check_class == "PHYSICAL" and r.status == "FAIL"


# ── hub_overrides: 적용 마킹·참조 불능·enum (spec §5.3, §6.3 해당분) ─────────
def test_override_1행은_is_override_마킹과_rule_보존(fx):
    ov = pd.DataFrame([{"place_id": "PB_2", "hub_class": "CROSSING",
                        "reason": "테스트", "source": "unit"}])
    out = q.apply_hub_overrides(q.qualify(fx["metrics"], _PARAMS["thresholds"]), ov)
    row = out[out["place_id"] == "PB_2"].iloc[0]
    assert row["hub_class"] == "CROSSING" and bool(row["is_override"])
    assert row["hub_class_rule"] == "NONE" and int(row["override_row"]) == 1
    assert chk.c008_override_integrity(out, ov, fx["places"]).status == "PASS"
    # 적용 건수 == override 행수 위반(마킹 소실) 검출
    tampered = out.copy()
    tampered["is_override"] = False
    assert chk.c008_override_integrity(tampered, ov, fx["places"]).status == "FAIL"


def test_override_참조_불능과_enum_위반은_빌드_즉사(fx):
    rule = q.qualify(fx["metrics"], _PARAMS["thresholds"])
    with pytest.raises(ContractViolation):
        q.apply_hub_overrides(rule, pd.DataFrame([{
            "place_id": "PB_없음", "hub_class": "CROSSING", "reason": "", "source": ""}]))
    with pytest.raises(ContractViolation):
        q.apply_hub_overrides(rule, pd.DataFrame([{
            "place_id": "PB_1", "hub_class": "SUPER_HUB", "reason": "", "source": ""}]))


# ── build_lspace 구조 계약 ───────────────────────────────────────────────────
def test_build_lspace_붕괴_dedup_정렬_universe():
    places = pd.DataFrame({
        "place_id": ["A", "B", "C"], "name_norm": ["가", "나", "다"],
        "lat_centroid": [_LAT0, _north(500), _north(1000)], "lon_centroid": [_LON0] * 3})
    stop2place = pd.Series({"p1": "A", "p2": "A", "p3": "B", "p4": "C"})
    backbone = pd.DataFrame([
        # PT1(R1): A,A,B → 붕괴 → A-B
        ("R1", "PT1", 1, "p1"), ("R1", "PT1", 2, "p2"), ("R1", "PT1", 3, "p3"),
        # PT2(R2): B,C,A → B-C, A-C (a<b 정렬)
        ("R2", "PT2", 1, "p3"), ("R2", "PT2", 2, "p4"), ("R2", "PT2", 3, "p1"),
        # PT3(R9): universe 밖 — 기여 0
        ("R9", "PT3", 1, "p1"), ("R9", "PT3", 2, "p4"),
        # PT4(R1): A,B,A — 비연속 재방문은 같은 무향 엣지로 dedup
        ("R1", "PT4", 1, "p1"), ("R1", "PT4", 2, "p3"), ("R1", "PT4", 3, "p2"),
    ], columns=["route", "pattern_id", "seq", "stop_id"])
    edges = m.build_lspace(backbone, stop2place, {"R1", "R2"}, places)
    assert list(edges.columns) == m.EDGE_COLUMNS
    assert (edges["place_a"] < edges["place_b"]).all()
    assert not edges.duplicated(["place_a", "place_b"]).any()
    assert set(map(tuple, edges[["place_a", "place_b"]].to_numpy())) \
        == {("A", "B"), ("B", "C"), ("A", "C")}
    ab = edges[(edges["place_a"] == "A") & (edges["place_b"] == "B")].iloc[0]
    assert int(ab["n_patterns"]) == 2 and ab["routes"] == "R1"
    assert (edges["gap_m"] > 0).all()
    assert chk.c005_edge_integrity(edges, places, {"R1", "R2"}).status == "PASS"


def test_미지_universe는_ContractViolation():
    catalog = pd.DataFrame({"route": ["10"], "route_class": ["general"]})
    with pytest.raises(ContractViolation):
        m.universe_route_set(catalog, "unknown_universe")
    assert m.universe_route_set(catalog, "general_before") == {"10"}


# ── qualify 순수 함수 ────────────────────────────────────────────────────────
def test_qualify_자격식():
    mm = pd.DataFrame({
        "place_id": ["c", "t", "n1", "n2"],
        "D": [3, 6, 2, 5], "A": [3, 2, 2, 2], "L_star": [0, 7, 0, 3]})
    out = q.qualify(mm, _PARAMS["thresholds"]).set_index("place_id")
    assert out.loc["c", "hub_class_rule"] == "CROSSING"
    assert out.loc["t", "hub_class_rule"] == "TERMINAL"    # D6·A2·L*7
    assert out.loc["n1", "hub_class_rule"] == "NONE"       # D2
    assert out.loc["n2", "hub_class_rule"] == "NONE"       # L*3 < 5
    with pytest.raises(ContractViolation):
        q.qualify(mm, {"crossing_min_degree": 3})          # thresholds 키 누락


# ── 스팟 DIFF의 비수치 동등 비교(dict) — spec §5.7 judge 요구 ────────────────
def test_diff_judge_dict_동등_비교():
    from bts.checks import diff
    baseline = {"spot": {"태화로터리": {"D": 5, "A": 2, "L": 0}}}
    r = diff.judge("D-T-B-001", {"D": 5, "A": 2, "L": 0}, "spot.태화로터리",
                   baseline=baseline, kds=[], make_stub_on_unexplained=False)
    assert r.status == "MATCH"
    r2 = diff.judge("D-T-B-001", {"D": 6, "A": 2, "L": 0}, "spot.태화로터리",
                    baseline=baseline, kds=[], make_stub_on_unexplained=False)
    assert r2.status == "UNEXPLAINED"


# ── 표현 계약: 컬럼·지표 사전 lint (design.md §8, DoD 8) ─────────────────────
def test_s03_산출_컬럼과_semantics는_lint_통과():
    names = (m.METRIC_COLUMNS + m.EDGE_COLUMNS + m.GAP_COLUMNS
             + m.SENSITIVITY_COLUMNS + q.QUALIFICATION_COLUMNS
             + list(m.GAP_REASONS) + list(q.HUB_CLASSES))
    assert lint.find_forbidden(names) == []
    assert lint.find_forbidden(m._SEMANTICS_TEXT.splitlines()) == []
