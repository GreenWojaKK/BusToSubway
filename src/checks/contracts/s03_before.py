"""s03_hub / before 필수 검증 — C-S03-B-001~010, P-S03-B-001, D-S03-B-001~009
(verification.md §5.6 + stage2_place_hub_spec.md §5.7).

001~004는 verification.md §5.6 기정의, 005~010·P-001·D-002~009는 명세 §5.7 발번.
개별 체크 함수는 데이터프레임을 직접 받는다 — 위반 주입 테스트
(tests/unit/test_s03_violations.py)가 단독 호출한다.

D-S03-B-001~009는 첫 실행에서 UNEXPLAINED가 예상 상태다(spec §0.1 사전 캘리브레이션 —
498은 이식된 개념·제약을 만족하는 어떤 격자 변형으로도 재현되지 않았다). 설명은
DIFF-0003-hub-signals.md 1문서로 통합한다(spec §5.8). 기준값 수치 수정 금지.
D-S03-B-002~009의 기준값은 memory_sourced(기준 입력 묶음 명시 주석) — 신호 전용.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import paths
from checks import core, diff
from s03_hub import metrics as metrics_mod
from s03_hub import qualify as qualify_mod

_SRC = "stage2_place_hub_spec.md §5.7; verification.md §5.6"
_INVESTIGATION_METRIC = "hub-signals"  # D-S03-B-001~009 통합 조사 메모 슬러그 (spec §5.8)

# 스팟 체크 ID 발번 (spec §5.7 표 순서 고정 — baseline before.hub.spot 키)
_SPOT_CHECKS = (("D-S03-B-006", "구언양버스터미널"),
                ("D-S03-B-007", "고속버스터미널앞"),
                ("D-S03-B-008", "태화로터리"),
                ("D-S03-B-009", "신복로터리"))


def _dump(r: core.CheckResult, vdir, df) -> None:
    """실패 표본 의무(verification.md §7 규율 6). 재검증 경로는 기존 덤프 재사용."""
    if vdir is not None and r.failed and df is not None and len(df):
        try:
            r.sample_path = core.dump_sample(vdir, r.check_id, df)
        except paths.WriteViolation:
            existing = Path(vdir) / "_debug" / f"{r.check_id}_sample.csv"
            r.sample_path = str(existing) if existing.exists() else None


# ── CONTRACT ─────────────────────────────────────────────────────────────────
def c001_full_metrics(metrics: pd.DataFrame, places: pd.DataFrame,
                      vdir=None) -> core.CheckResult:
    """C-S03-B-001: len(place_metrics) == len(places) — 전수(사전 임계 필터 금지)."""
    mset, pset = set(metrics["place_id"].astype(str)), set(places["place_id"].astype(str))
    obs = {"metrics_rows": int(len(metrics)), "places_rows": int(len(places)),
           "missing_places": len(pset - mset), "alien_places": len(mset - pset),
           "place_id_dup": int(metrics["place_id"].duplicated().sum())}
    exp = {"metrics_rows": int(len(places)), "places_rows": int(len(places)),
           "missing_places": 0, "alien_places": 0, "place_id_dup": 0}
    r = core.check_true("C-S03-B-001", "CONTRACT", obs == exp, obs, exp,
                        "verification.md §5.6 (D≥3 사전 필터가 시골을 배제한 교훈의 구조화)")
    _dump(r, vdir, places[places["place_id"].astype(str).isin(pset - mset)])
    return r


def c002_lstar_identity(metrics: pd.DataFrame, lstar_gate_min_degree: int,
                        vdir=None) -> core.CheckResult:
    """C-S03-B-002: D < gate → L*==0 AND D >= gate → L*==L 전행."""
    gate = int(lstar_gate_min_degree)
    d = metrics["D"].astype(int)
    l = metrics["L"].astype(int)
    ls = metrics["L_star"].astype(int)
    bad = metrics[((d < gate) & (ls != 0)) | ((d >= gate) & (ls != l))]
    r = core.check_true("C-S03-B-002", "CONTRACT", len(bad) == 0,
                        {"violating_rows": int(len(bad)), "gate": gate},
                        {"violating_rows": 0},
                        "verification.md §5.6; ADR-008 (L* 게이트 항등)")
    _dump(r, vdir, bad)
    return r


def c003_qualification_recompute(qualification: pd.DataFrame, metrics: pd.DataFrame,
                                 thresholds: dict, vdir=None) -> core.CheckResult:
    """C-S03-B-003: qualification == qualify(metrics, params) 재계산 전행 일치
    (override 행 제외한 hub_class 포함) + override 행 전부 is_override=True·hub_class_rule 보존."""
    rec = qualify_mod.qualify(metrics, thresholds).set_index("place_id")
    q = qualification.set_index("place_id")
    joined = q.join(rec, rsuffix="_rec", how="outer")
    rule_mism = joined[(joined["hub_class_rule"] != joined["hub_class_rule_rec"])
                       | (joined["is_crossing"] != joined["is_crossing_rec"])
                       | (joined["is_terminal"] != joined["is_terminal_rec"])]
    ov = q["is_override"].astype(bool)
    class_mism = q[~ov & (q["hub_class"] != q["hub_class_rule"])]
    ov_unmarked = q[ov & q["override_row"].isna()]
    obs = {"rows": int(len(qualification)), "metrics_rows": int(len(metrics)),
           "rule_recompute_mismatch": int(len(rule_mism)),
           "non_override_class_mismatch": int(len(class_mism)),
           "override_rows_unnumbered": int(len(ov_unmarked))}
    exp = {"rows": int(len(metrics)), "metrics_rows": int(len(metrics)),
           "rule_recompute_mismatch": 0, "non_override_class_mismatch": 0,
           "override_rows_unnumbered": 0}
    r = core.check_true("C-S03-B-003", "CONTRACT", obs == exp, obs, exp,
                        "verification.md §5.6 (판정 = params 순수 함수 — 재계산 대조)")
    bad = rule_mism if len(rule_mism) else class_mism
    _dump(r, vdir, bad.reset_index() if len(bad) else None)
    return r


def c004_arm_leq_degree(metrics: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S03-B-004: A ≤ D 전행 (D≥1에서 A≥1 포함) — 기하 제약."""
    d = metrics["D"].astype(int)
    a = metrics["A"].astype(int)
    bad = metrics[(a > d) | ((d >= 1) & (a < 1))]
    r = core.check_true("C-S03-B-004", "CONTRACT", len(bad) == 0,
                        {"violating_rows": int(len(bad))}, {"violating_rows": 0},
                        "verification.md §5.6; ADR-009 (1 <= A <= D for D>=1)")
    _dump(r, vdir, bad)
    return r


def c005_edge_integrity(edges: pd.DataFrame, places: pd.DataFrame,
                        universe_routes: set, vdir=None) -> core.CheckResult:
    """C-S03-B-005: self-loop 0; place_a < place_b·유일; 양끝 FK ⊆ places;
    routes 전부 universe 내 — universe 밖 route 기여 0."""
    place_ids = set(places["place_id"].astype(str))
    self_loops = int((edges["place_a"] == edges["place_b"]).sum())
    unsorted_rows = int((edges["place_a"].astype(str) >= edges["place_b"].astype(str)).sum())
    dup = int(edges.duplicated(subset=["place_a", "place_b"]).sum())
    fk_bad = edges[~(edges["place_a"].astype(str).isin(place_ids)
                     & edges["place_b"].astype(str).isin(place_ids))]
    alien = set()
    for rs in edges["routes"].astype(str):
        alien |= {r for r in rs.split("|") if r and r not in universe_routes}
    bad_route_rows = edges[edges["routes"].astype(str).map(
        lambda rs: any(r and r not in universe_routes for r in rs.split("|")))]
    obs = {"self_loops": self_loops, "unsorted_pairs": unsorted_rows, "dup_pairs": dup,
           "fk_dangling_rows": int(len(fk_bad)),
           "out_of_universe_routes": sorted(alien)[:10],
           "n_out_of_universe_routes": len(alien),
           "n_universe_routes": len(universe_routes)}
    exp = {"self_loops": 0, "unsorted_pairs": 0, "dup_pairs": 0, "fk_dangling_rows": 0,
           "out_of_universe_routes": [], "n_out_of_universe_routes": 0,
           "n_universe_routes": len(universe_routes)}
    r = core.check_true("C-S03-B-005", "CONTRACT", obs == exp, obs, exp,
                        "stage2_place_hub_spec.md §5.7; ADR-010 (universe 준수)")
    bad = bad_route_rows if len(bad_route_rows) else fk_bad
    _dump(r, vdir, bad)
    return r


def c006_domain_identities(metrics: pd.DataFrame, k_max: int, vdir=None) -> core.CheckResult:
    """C-S03-B-006: A==0 ⇔ D==0; D==0 → L==0; L ∈ [0, k_max]; in_lspace ⇔ D≥1."""
    d = metrics["D"].astype(int)
    a = metrics["A"].astype(int)
    l = metrics["L"].astype(int)
    il = metrics["in_lspace"].astype(bool)
    bad = metrics[((a == 0) != (d == 0)) | ((d == 0) & (l != 0))
                  | (l < 0) | (l > int(k_max)) | (il != (d >= 1))]
    r = core.check_true("C-S03-B-006", "CONTRACT", len(bad) == 0,
                        {"violating_rows": int(len(bad)), "k_max": int(k_max)},
                        {"violating_rows": 0},
                        "stage2_place_hub_spec.md §5.7 (정의역 항등)")
    _dump(r, vdir, bad)
    return r


def _expected_grid(params: dict) -> list[tuple]:
    """params → 의무 격자 (mask, dom, k, theta) 목록 — lifetime 12행 + θ 변형."""
    grid = params["sensitivity_grid"]
    adopted_theta = float(params["arm_theta_deg"])
    lt = params["lifetime"]
    adopted = (str(lt["mask_mode"]), str(lt["dominance"]), int(lt["k_max"]), adopted_theta)
    out = [(str(m), str(d), int(k), adopted_theta)
           for m in grid["mask_mode"] for d in grid["dominance"] for k in grid["k_max"]]
    out += [(adopted[0], adopted[1], adopted[2], float(t))
            for t in grid["arm_theta_deg"] if float(t) != adopted_theta]
    return out


def c007_sensitivity(sens: pd.DataFrame, qualification: pd.DataFrame,
                     params: dict, vdir=None) -> core.CheckResult:
    """C-S03-B-007: 의무 격자 전행 존재; is_adopted 정확히 1행 == params 조합;
    채택 행 수치 == hub_qualification(override 미적용 기준 = hub_class_rule) 재계산."""
    s = sens.copy()
    s["k_max"] = s["k_max"].astype(int)
    s["arm_theta_deg"] = s["arm_theta_deg"].astype(float)
    for c in ("n_crossing", "n_terminal", "n_hub_qualified"):
        s[c] = s[c].astype(int)
    adopted_flag = s["is_adopted"].astype(str).str.lower().isin(("true", "1"))

    expected = _expected_grid(params)
    have = {(str(r.mask_mode), str(r.dominance), int(r.k_max), float(r.arm_theta_deg))
            for r in s.itertuples()}
    missing = [v for v in expected if v not in have]

    lt = params["lifetime"]
    adopted = (str(lt["mask_mode"]), str(lt["dominance"]), int(lt["k_max"]),
               float(params["arm_theta_deg"]))
    ad_rows = s[adopted_flag]
    ad_combo_ok = (len(ad_rows) == 1 and
                   (str(ad_rows.iloc[0]["mask_mode"]), str(ad_rows.iloc[0]["dominance"]),
                    int(ad_rows.iloc[0]["k_max"]),
                    float(ad_rows.iloc[0]["arm_theta_deg"])) == adopted)

    nc = int((qualification["hub_class_rule"] == "CROSSING").sum())
    nt = int((qualification["hub_class_rule"] == "TERMINAL").sum())
    counts_ok = (len(ad_rows) == 1
                 and int(ad_rows.iloc[0]["n_crossing"]) == nc
                 and int(ad_rows.iloc[0]["n_terminal"]) == nt
                 and int(ad_rows.iloc[0]["n_hub_qualified"]) == nc + nt)

    obs = {"rows": int(len(s)), "missing_variants": [str(v) for v in missing],
           "n_adopted_rows": int(adopted_flag.sum()),
           "adopted_combo_ok": bool(ad_combo_ok), "adopted_counts_ok": bool(counts_ok),
           "recomputed": {"n_crossing": nc, "n_terminal": nt, "n_hub_qualified": nc + nt}}
    exp = {"rows": len(expected), "missing_variants": [], "n_adopted_rows": 1,
           "adopted_combo_ok": True, "adopted_counts_ok": True,
           "recomputed": {"n_crossing": nc, "n_terminal": nt, "n_hub_qualified": nc + nt}}
    r = core.check_true("C-S03-B-007", "CONTRACT", obs == exp, obs, exp,
                        "stage2_place_hub_spec.md §5.4 (정의 감도 의무 — 1급 산출물)")
    _dump(r, vdir, s[~s.apply(lambda row: (str(row["mask_mode"]), str(row["dominance"]),
                                           int(row["k_max"]), float(row["arm_theta_deg"]))
                              in set(expected), axis=1)] if len(s) else None)
    return r


def c008_override_integrity(qualification: pd.DataFrame, overrides: pd.DataFrame,
                            places: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S03-B-008: hub_overrides 무결 — 참조 place_id 해소 100%; hub_class enum;
    적용 건수 == override 데이터 행수."""
    place_ids = set(places["place_id"].astype(str))
    dangling = sorted(set(overrides["place_id"].astype(str)) - place_ids) \
        if len(overrides) else []
    bad_cls = sorted(set(overrides["hub_class"].astype(str))
                     - set(qualify_mod.HUB_CLASSES)) if len(overrides) else []
    n_applied = int(qualification["is_override"].astype(bool).sum())
    qual_bad_cls = sorted(set(qualification["hub_class"].astype(str))
                          - set(qualify_mod.HUB_CLASSES))
    obs = {"override_rows": int(len(overrides)), "applied_rows": n_applied,
           "dangling_place_ids": dangling, "bad_hub_class": bad_cls,
           "qual_bad_hub_class": qual_bad_cls}
    exp = {"override_rows": int(len(overrides)), "applied_rows": int(len(overrides)),
           "dangling_place_ids": [], "bad_hub_class": [], "qual_bad_hub_class": []}
    r = core.check_true("C-S03-B-008", "CONTRACT", obs == exp, obs, exp,
                        "stage2_place_hub_spec.md §5.3 (빈 override면 vacuous PASS)")
    _dump(r, vdir, qualification[qualification["is_override"].astype(bool)])
    return r


def c009_class_accounting(qualification: pd.DataFrame, places: pd.DataFrame,
                          thresholds: dict, vdir=None) -> core.CheckResult:
    """C-S03-B-009: n_CROSSING + n_TERMINAL + n_NONE == len(places);
    기본 임계(상호 배타 조건 성립 시)에서 is_crossing & is_terminal 동시 True 0."""
    vc = qualification["hub_class"].astype(str).value_counts()
    parts = {c: int(vc.get(c, 0)) for c in qualify_mod.HUB_CLASSES}
    both = int((qualification["is_crossing"].astype(bool)
                & qualification["is_terminal"].astype(bool)).sum())
    disjoint = int(thresholds["crossing_min_arms"]) > int(thresholds["terminal_max_arms"])
    obs = {"parts": parts, "sum": sum(parts.values()), "places": int(len(places)),
           "both_true": both, "thresholds_disjoint": disjoint}
    exp = {"parts": parts, "sum": int(len(places)), "places": int(len(places)),
           "both_true": 0 if disjoint else both, "thresholds_disjoint": disjoint}
    note = ("" if disjoint else
            f"임계 겹침 구성(crossing_min_arms <= terminal_max_arms) — CROSSING 우선 적용, "
            f"동시 True {both}건 기록 (spec §5.3)")
    r = core.check_true("C-S03-B-009", "CONTRACT", obs == exp, obs, exp,
                        "stage2_place_hub_spec.md §5.7 (accounting)", note=note)
    _dump(r, vdir, qualification[qualification["is_crossing"].astype(bool)
                                 & qualification["is_terminal"].astype(bool)]
          if both else None)
    return r


def c010_gap_accounting(gap: pd.DataFrame, metrics: pd.DataFrame,
                        vdir=None) -> core.CheckResult:
    """C-S03-B-010: diag_lspace_gap 사유별 합 == (D==0) place 수 (accounting)."""
    n_d0 = int((metrics["D"].astype(int) == 0).sum())
    parts = {str(k): int(v) for k, v in
             gap["reason"].astype(str).value_counts().sort_index().items()} \
        if len(gap) else {}
    r = core.accounting("C-S03-B-010", parts or {"(no_rows)": 0}, n_d0,
                        "stage2_place_hub_spec.md §5.5; ADR-010 (공백 회계)")
    if r.failed and vdir is not None:
        d0 = set(metrics.loc[metrics["D"].astype(int) == 0, "place_id"].astype(str))
        missing = metrics[metrics["place_id"].astype(str).isin(
            d0 - set(gap["place_id"].astype(str)))]
        _dump(r, vdir, missing if len(missing) else gap)
    return r


# ── PHYSICAL (WARN + --ack) ──────────────────────────────────────────────────
def p001_edge_gap(edges: pd.DataFrame, params: dict, vdir=None) -> core.CheckResult:
    """P-S03-B-001: 전 엣지 gap_m <= lspace_edge_max_m — 미실측 임계(ADR-007 계보).

    universe 내 최장 실세계 개연 도약 = 울주08 DRT 순회 12,396m. 위반 = 사상/시퀀스 결함
    수준 도약 신호. params 미등재 시 SKIP + 측정값 기록(s01 P-001 선례).
    """
    cid = "P-S03-B-001"
    max_gap = float(edges["gap_m"].astype(float).max()) if len(edges) else 0.0
    thr = params.get("lspace_edge_max_m")
    if thr is None:
        return core.CheckResult(
            check_id=cid, check_class="PHYSICAL", severity="WARN", status="SKIP",
            observed={"max_gap_m": round(max_gap, 1)},
            expected="params.stages.s03_hub.lspace_edge_max_m 등재 필요",
            source="stage2_place_hub_spec.md §5.7 (미실측 임계)",
            failure_means=["param_sensitivity"],
            note="params 미등재 — 측정값만 기록하고 SKIP")
    over = edges[edges["gap_m"].astype(float) > float(thr)]
    r = core.check_true(cid, "PHYSICAL", len(over) == 0,
                        {"over_threshold_edges": int(len(over)),
                         "max_gap_m": round(max_gap, 1), "threshold_m": float(thr)},
                        {"over_threshold_edges": 0},
                        "stage2_place_hub_spec.md §5.7 (미실측 임계 — WARN, ADR-007 계보)",
                        failure_means=["logic_bug", "param_sensitivity"],
                        note="위반 = 사상/시퀀스 결함 수준 도약 신호 — 수용이면 --ack")
    _dump(r, vdir, over)
    return r


# ── DIFF (SIGNAL) ────────────────────────────────────────────────────────────
def d_spot(cid: str, metrics: pd.DataFrame, name: str) -> core.CheckResult:
    """D-S03-B-006~009: 스팟 (D,A,L) 전항 일치 — 해당 name_norm place 복수면
    어느 하나라도 전항 일치 시 MATCH; 불일치면 최근접 후보 튜플 기록(조사 메모 설명 재료);
    이름 부재 시 observed='name_absent' → UNEXPLAINED."""
    key = f"before.hub.spot.{name}"
    exp = {k: int(v) for k, v in diff.reference_value(diff.load_reference_values(), key).items()
           if k in ("D", "A", "L")}
    cand = metrics[metrics["name_norm"].astype(str) == name]
    n_cand = int(len(cand))
    if not n_cand:
        observed: object = "name_absent"
    else:
        tuples = [{"D": int(t.D), "A": int(t.A), "L": int(t.L)} for t in cand.itertuples()]
        match = next((t for t in tuples if t == exp), None)
        observed = match if match is not None else min(
            tuples, key=lambda t: (abs(t["D"] - exp["D"]) + abs(t["A"] - exp["A"])
                                   + abs(t["L"] - exp["L"]), t["D"], t["A"], t["L"]))
    r = diff.judge(cid, observed, key, metric=_INVESTIGATION_METRIC)
    extra = f"동명 place 후보 {n_cand}개 (분리 place 대응 술어 — spec §5.7)"
    r.note = f"{r.note} | {extra}" if r.note else extra
    return r


def run(ctx) -> list:
    metrics = ctx.df("place_metrics.parquet")
    qualification = ctx.df("hub_qualification.parquet")
    edges = ctx.df("l_space_place_edges.parquet")
    sens = ctx.df("sensitivity.csv")
    gap = ctx.df("diag_lspace_gap.csv")
    places = ctx.input_df("s02_place", "places.parquet")
    catalog = ctx.input_df("s01_canonical", "route_catalog.parquet")
    overrides = qualify_mod.load_hub_overrides()
    uroutes = metrics_mod.universe_route_set(catalog, str(ctx.params["universe"]))
    thresholds = ctx.params["thresholds"]
    v = ctx.vdir

    d = metrics["D"].astype(int)
    n_hub = int(qualification["hub_class"].isin(["CROSSING", "TERMINAL"]).sum())

    results = [
        c001_full_metrics(metrics, places, v),
        c002_lstar_identity(metrics, int(ctx.params["lstar_gate_min_degree"]), v),
        c003_qualification_recompute(qualification, metrics, thresholds, v),
        c004_arm_leq_degree(metrics, v),
        c005_edge_integrity(edges, places, uroutes, v),
        c006_domain_identities(metrics, int(ctx.params["lifetime"]["k_max"]), v),
        c007_sensitivity(sens, qualification, ctx.params, v),
        c008_override_integrity(qualification, overrides, places, v),
        c009_class_accounting(qualification, places, thresholds, v),
        c010_gap_accounting(gap, metrics, v),
        p001_edge_gap(edges, ctx.params, v),
        diff.judge("D-S03-B-001", n_hub, "before.hub.qualified",
                   metric=_INVESTIGATION_METRIC),
        diff.judge("D-S03-B-002",
                   int((qualification["hub_class"] == "CROSSING").sum()),
                   "before.hub.decomposition.crossing", metric=_INVESTIGATION_METRIC),
        diff.judge("D-S03-B-003",
                   int((qualification["hub_class"] == "TERMINAL").sum()),
                   "before.hub.decomposition.terminal", metric=_INVESTIGATION_METRIC),
        diff.judge("D-S03-B-004", int((d <= 2).sum()),
                   "before.hub.decomposition.peripheral_d_le_2",
                   metric=_INVESTIGATION_METRIC),
        diff.judge("D-S03-B-005", int((d == 2).sum()),
                   "before.hub.decomposition.d_eq_2", metric=_INVESTIGATION_METRIC),
    ]
    for r in results[-2:]:
        extra = ("분모 우주 상이(선행 기준 1,759 vs 본 설계 place 전량) — 비율 보정 해석 필수 "
                 "(spec §5.8-3, ADR-010)")
        r.note = f"{r.note} | {extra}" if r.note else extra
    results += [d_spot(cid, metrics, name) for cid, name in _SPOT_CHECKS]
    return results
