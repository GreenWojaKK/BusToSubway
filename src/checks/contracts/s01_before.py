"""s01_canonical / before 필수 검증 — C-S01-B-001~011, P-S01-B-001~003, D-S01-B-001~007
(verification.md §5.3).

expected는 전부 감사 실측(reference/audit/*.md) 또는 prior_baseline이 출처다.
개별 체크 함수는 데이터프레임을 직접 받는다 — 위반 주입 테스트가 단독 호출한다.
D-S01-B-004/005/006은 known_deviations(KD-0001~0003, hypothesis) 사전 등재로
EXPLAINED가 기대 상태다 — 신규 UNEXPLAINED 0이 목표.

출처 약어: [SB]=audit/schedule_before.md, [VT]=audit/variant_tags.md, [RS]=audit/routes_stops.md.
아래 상수는 감사 실측 기준값이다(임계값 아님 — 튜닝 대상 아님).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import paths
from checks import core, diff
from dataio import normalize, raw_variant_tags

# ── 감사 실측 감사 기준 상수 ──────────────────────────────────────────────────────
_PATTERNS_TOTAL = 487                                   # [SB§2]
_TAGGED_PATTERNS = 481                                  # [VT§1]
_DRT_PATTERNS = 6                                       # [SB§3] ACC0 비결정 6
_SUPPORT_IDS = ["BR_TAGO_USB103000132", "BR_TAGO_USB103002362",  # [VT§4] 지원 6패턴 exact
                "BR_TAGO_USB103002363", "BR_TAGO_USB103002364",
                "BR_TAGO_USB103008023", "BR_TAGO_USB103009242"]
_SUPPORT_TRIPS = 57                                     # [VT§4]
_TAGGED_TRIPS = 7_568                                   # [VT§4]
_TRIPS_TOTAL = 7_625                                    # [SB§2]
_BASE_NORM = {"main_raw_selfref": 92, "circular_selfref_preserved": 34,
              "rechained": 4}                           # [VT§3]
_CANONICAL_PARTS = {"main": 187, "circular_baseless": 66,
                    "short_turn": 112, "branch": 14}    # [VT§6] 379 분해
_CANONICAL_TOTAL = 379                                  # [VT§6]
_DISPOSITION_PARTS = {"canonical": 379, "excl_circular_with_base": 37,
                      "excl_detour": 51, "excl_extension": 10,
                      "excl_duplicate": 2, "excl_anomaly": 2,
                      "support": 6}                     # [VT§2,§3] 487 전수 분해
_ROUTES_TAGGED = 184                                    # [VT§1]
_PS_DUP = {"dup_patterns": 55, "closures": 8}           # [VT§5] 대표 시퀀스 내 stop 중복
_DUP_ROUTE_GROUPS = ["22|977", "22|977", "527|537|808", "941|948"]   # [SB§6] 정렬 표기
_VT_DUP_ROUTES = ["323"]                                # [VT§2] role=duplicate는 323 내부
_CATALOG = {"rows": 191, "no_schedule": ["김해공항"]}    # [RS§4][SB§8]
_CLASS_DIST = {"express": 14, "general": 160, "limousine": 1,
               "support": 6, "ulju": 10}                # [RS§4] (general+ulju=170)
_IS_LOOP_X = {"circular_not_loop": 3, "main_loop": 11}  # [VT§2] is_loop≠circular 실측
_ROUTE_ANCHOR_ROLES = ("main", "circular")              # [VT§2] 'route에 main/circular ≥1' 어휘
_DERIVED_ROLES = ("short_turn", "branch", "detour", "extension")     # [VT§3]
# ADR-006 실측 검증 규칙(2026-07-04 v002 재계산): backbone 미커버 = circular 자기참조 34 route exact.
# design.md §5 s01 ⑥의 '184 전 커버' 가설은 반증됨 — 부분 커버는 canonical 379 검증 규칙의 귀결.
_BACKBONE_ROUTES_COVERED = 150
_BACKBONE_UNCOVERED_ROUTES = [
    "111", "1421", "31", "32", "343", "36", "426", "53",
    "913", "921", "922", "923", "925", "927", "928", "929",
    "931", "932", "944", "945", "951", "975", "976", "978", "981",
    "울주01", "울주02", "울주03", "울주04", "울주05", "울주06", "울주07", "울주09", "울주10"]
_CIRCULAR_BASELESS = "circular_baseless"
_CIRCULAR_WITH_BASE = "circular_with_base"


def _dump(r: core.CheckResult, vdir, df) -> None:
    """실패 표본 의무(verification.md §7 규율 6) — FAIL이면 _debug/ 덤프.

    재검증(run checks) 경로에서는 게시본이 불변이라 쓰기가 거부된다 —
    빌드 시점 표본이 이미 존재하므로 기존 경로를 가리키고 넘어간다.
    """
    if vdir is not None and r.failed and df is not None and len(df):
        try:
            r.sample_path = core.dump_sample(vdir, r.check_id, df)
        except paths.WriteViolation:
            existing = Path(vdir) / "_debug" / f"{r.check_id}_sample.csv"
            r.sample_path = str(existing) if existing.exists() else None


def _reps(pattern_stops: pd.DataFrame) -> pd.Series:
    """pattern_stops → {pattern_id: 대표 정차열 tuple}."""
    return (pattern_stops.sort_values(["pattern_id", "seq"], kind="mergesort")
            .groupby("pattern_id")["stop_id"].agg(tuple))


def _role_scope(patterns: pd.DataFrame) -> pd.Series:
    """role_scope 재계산 — circular를 raw base 결측 기준으로 분해 (design.md §5 s01 ⑥)."""
    circ = patterns["role"] == "circular"
    return patterns["role"].where(
        ~circ, np.where(patterns["base_pattern_id_raw"].isna(),
                        _CIRCULAR_BASELESS, _CIRCULAR_WITH_BASE))


# ── CONTRACT ─────────────────────────────────────────────────────────────────
def c001_pattern_reconstruction(patterns: pd.DataFrame, pattern_stops: pd.DataFrame,
                                st: pd.DataFrame, ev_ids: dict, vt: pd.DataFrame,
                                vdir=None) -> core.CheckResult:
    """C-S01-B-001: 패턴 487·대표 시퀀스의 단일 기준 재확인.

    결정 패턴: 모든 trip 정차열 == 대표(유일성 재확인). tags n_stops == 대표 길이 전행.
    ACC0 비결정 6: 대표 == evidence 합집합 순회(is_drt), distinct == schedule 합집합 distinct.
    """
    rep = _reps(pattern_stops)
    sd = st.sort_values(["trip_id", "seq"], kind="mergesort")
    trip_pat = sd.groupby("trip_id")["stop_id"].agg(tuple)
    pid_of = sd.drop_duplicates("trip_id").set_index("trip_id")["pattern_id"]
    uniq = (pd.DataFrame({"p": trip_pat, "pattern_id": pid_of})
            .groupby("pattern_id")["p"].agg(lambda x: set(x)))
    drt = patterns.set_index("pattern_id")["is_drt"].astype(bool)

    nondrt_bad = [pid for pid in rep.index
                  if not drt.get(pid, False) and uniq.get(pid) != {rep[pid]}]
    drt_ids = sorted(drt[drt].index)
    drt_nonacc0 = [p for p in drt_ids if not p.startswith("BR_ACC0_")]
    drt_ev_bad = [p for p in drt_ids if tuple(ev_ids.get(p, ())) != rep.get(p)]
    drt_set_bad = [p for p in drt_ids
                   if set(ev_ids.get(p, ())) != set().union(*(set(s) for s in uniq[p]))]
    tag_len = vt.set_index("pattern_id")["n_stops"].astype(int)
    nstops_bad = int((tag_len != rep.map(len).reindex(tag_len.index)).sum())

    obs = {"patterns": int(len(patterns)), "nondrt_rep_mismatch": len(nondrt_bad),
           "drt_count": len(drt_ids), "drt_non_acc0": len(drt_nonacc0),
           "drt_evidence_mismatch": len(drt_ev_bad), "drt_union_mismatch": len(drt_set_bad),
           "tags_nstops_mismatch": nstops_bad}
    exp = {"patterns": _PATTERNS_TOTAL, "nondrt_rep_mismatch": 0,
           "drt_count": _DRT_PATTERNS, "drt_non_acc0": 0,
           "drt_evidence_mismatch": 0, "drt_union_mismatch": 0,
           "tags_nstops_mismatch": 0}
    r = core.check_true("C-S01-B-001", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §1, §4, §5",
                        note="울주01 실측: evidence 52엔트리/distinct 44 == schedule 합집합 44")
    _dump(r, vdir, patterns[patterns["pattern_id"].isin(nondrt_bad + drt_ev_bad + drt_set_bad)])
    return r


def c002_join_direction(patterns: pd.DataFrame, vt: pd.DataFrame,
                        ta: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S01-B-002: 조인 방향 검증 규칙 — tags 481 전건 매치, 미태깅 == 지원 6패턴 exact (57 trips)."""
    unmatched = sorted(set(vt["pattern_id"]) - set(patterns["pattern_id"]))
    support_ids = sorted(patterns.loc[patterns["role"] == "support", "pattern_id"])
    obs = {"tags_unmatched": len(unmatched), "support_ids": support_ids,
           "support_trips": int((ta["attribution"] == "support").sum())}
    exp = {"tags_unmatched": 0, "support_ids": _SUPPORT_IDS,
           "support_trips": _SUPPORT_TRIPS}
    r = core.check_true("C-S01-B-002", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §4")
    _dump(r, vdir, patterns[patterns["pattern_id"].isin(
        set(support_ids) ^ set(_SUPPORT_IDS))])
    return r


def c003_base_scope(patterns: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S01-B-003: base 정규화 role 스코프 — 379의 성립 조건.

    main 자기참조 92건만 base_ref=NaN, circular 자기참조 34건 보존('base 참조 있음'),
    재귀 해소 정확히 4건(체인 147 + detour→branch 2 + detour→short_turn 1), dangling 0.
    """
    main = patterns[patterns["role"] == "main"]
    circ = patterns[patterns["role"] == "circular"]
    role_of = patterns.set_index("pattern_id")["role"]
    raw_isna_of = patterns.set_index("pattern_id")["base_pattern_id_raw"].isna()
    derived = patterns[patterns["role"].isin(_DERIVED_ROLES)]
    resolved_roles = derived["base_ref_resolved"].map(role_of)
    # canonical 조상 술어 = main 또는 'base 없는 circular' (spec §5.1) —
    # role in {main, circular}만으로는 circular_with_base 종착을 검출하지 못한다
    canonical_ancestor = (resolved_roles == "main") | (
        (resolved_roles == "circular")
        & derived["base_ref_resolved"].map(raw_isna_of).fillna(False))
    obs = {
        "main_raw_selfref": int((main["base_pattern_id_raw"] == main["pattern_id"]).sum()),
        "main_base_ref_notna": int(main["base_ref"].notna().sum()),
        "circular_selfref_preserved": int((circ["base_ref"] == circ["pattern_id"]).sum()),
        "rechained": int((patterns["base_ref"].notna()
                          & (patterns["base_ref_resolved"] != patterns["base_ref"])).sum()),
        "derived_resolved_dangling": int(resolved_roles.isna().sum()),
        "derived_resolved_noncanonical": int((~canonical_ancestor
                                              & resolved_roles.notna()).sum()),
    }
    exp = {"main_raw_selfref": _BASE_NORM["main_raw_selfref"], "main_base_ref_notna": 0,
           "circular_selfref_preserved": _BASE_NORM["circular_selfref_preserved"],
           "rechained": _BASE_NORM["rechained"],
           "derived_resolved_dangling": 0, "derived_resolved_noncanonical": 0}
    r = core.check_true("C-S01-B-003", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §3",
                        note="전 role base==self→NaN 정규화 금지 — circular 자기참조 34 보존이 "
                             "canonical 379의 성립 조건 (design.md §13 치명 지적)")
    _dump(r, vdir, derived[~canonical_ancestor])
    return r


def c004_canonical_formula(patterns: pd.DataFrame, canonical: pd.DataFrame,
                           canonical_roles: list, vdir=None) -> core.CheckResult:
    """C-S01-B-004: canonical 재현식 — main 187 + base 없는 circular 66 + short_turn 112
    + branch 14 == 379 == canonical_rows 행수 == in_canonical 플래그."""
    rs = _role_scope(patterns)
    sel = rs.isin(canonical_roles)
    parts = {k: int(v) for k, v in rs[sel].value_counts().sort_index().items()}
    obs = {"parts": parts, "total": int(sel.sum()),
           "canonical_rows_file": int(len(canonical)),
           "in_canonical_flag_mismatch": int((patterns["in_canonical"].astype(bool) != sel).sum())}
    exp = {"parts": dict(sorted(_CANONICAL_PARTS.items())), "total": _CANONICAL_TOTAL,
           "canonical_rows_file": _CANONICAL_TOTAL, "in_canonical_flag_mismatch": 0}
    r = core.check_true("C-S01-B-004", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §6",
                        note="선정식 = role=='circular' & base_pattern_id_raw.isna() (66행). "
                             "전 role self→NaN이면 413≠379로 깨진다")
    _dump(r, vdir, patterns[patterns["in_canonical"].astype(bool) != sel])
    return r


def c005_disposition_accounting(disposition: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S01-B-005: 전수 분류표 회계 — canonical 379 + 제외 102 + support 6 == 487.

    observed에 분해를 그대로 기록 — 실패 시 어느 항이 새는지 즉시 판독(accounting 1급 어휘).
    """
    parts = {k: int(v) for k, v in disposition["disposition"].value_counts().sort_index().items()}
    obs = {"parts": parts, "sum": int(len(disposition))}
    exp = {"parts": dict(sorted(_DISPOSITION_PARTS.items())), "sum": _PATTERNS_TOTAL}
    r = core.check_true("C-S01-B-005", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §2, §3",
                        note="제외 role은 배제가 아니라 보존 — 시간층이 사용한다")
    _dump(r, vdir, disposition[~disposition["disposition"].isin(_DISPOSITION_PARTS)])
    return r


def c006_attribution_accounting(patterns: pd.DataFrame, ta: pd.DataFrame,
                                vdir=None) -> core.CheckResult:
    """C-S01-B-006: 전건 귀속 회계 — regular 481패턴·7,568 trips + support 6패턴·57 trips
    == 487패턴·7,625 trips; trip_attribution 7,625 전건·trip 유일."""
    tagged_trips = int((ta["attribution"] == "tagged").sum())
    support_trips = int((ta["attribution"] == "support").sum())
    obs = {"tagged_patterns": int((patterns["role"] != "support").sum()),
           "support_patterns": int((patterns["role"] == "support").sum()),
           "tagged_trips": tagged_trips, "support_trips": support_trips,
           "sum_trips": tagged_trips + support_trips,
           "ta_rows": int(len(ta)), "ta_trip_unique": int(ta["trip_id"].nunique())}
    exp = {"tagged_patterns": _TAGGED_PATTERNS, "support_patterns": len(_SUPPORT_IDS),
           "tagged_trips": _TAGGED_TRIPS, "support_trips": _SUPPORT_TRIPS,
           "sum_trips": _TRIPS_TOTAL, "ta_rows": _TRIPS_TOTAL, "ta_trip_unique": _TRIPS_TOTAL}
    r = core.check_true("C-S01-B-006", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §4")
    _dump(r, vdir, ta[ta.duplicated("trip_id", keep=False)])
    return r


def c007_route_coverage(patterns: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S01-B-007: 모든 route 184에 main 또는 circular ≥1; (route, direction_group)당 main ≤1."""
    tagged = patterns[patterns["role"] != "support"]
    has_mc = tagged.groupby("route")["role"].agg(
        lambda roles: bool(set(roles) & set(_ROUTE_ANCHOR_ROLES)))
    main_per_dg = (tagged[tagged["role"] == "main"]
                   .groupby(["route", "direction_group"]).size())
    obs = {"routes": int(tagged["route"].nunique()),
           "routes_without_main_or_circular": int((~has_mc).sum()),
           "route_dg_main_gt1": int((main_per_dg > 1).sum())}
    exp = {"routes": _ROUTES_TAGGED, "routes_without_main_or_circular": 0,
           "route_dg_main_gt1": 0}
    r = core.check_true("C-S01-B-007", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §2")
    _dump(r, vdir, tagged[tagged["route"].isin(has_mc[~has_mc].index)])
    return r


def c008_pattern_stops(pattern_stops: pd.DataFrame, stops: pd.DataFrame,
                       vt: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S01-B-008 [PC]: stop FK 100%; 태깅 481 대표 시퀀스 내 stop 중복 정확히 55패턴
    (순환 폐합 8 포함) — 유일성 assert가 존재하면 그것이 위반."""
    dangling = sorted(set(pattern_stops["stop_id"]) - set(stops["stop_id"]))
    tagged_ids = set(vt["pattern_id"])
    ps_t = pattern_stops[pattern_stops["pattern_id"].isin(tagged_ids)]
    g = ps_t.sort_values(["pattern_id", "seq"], kind="mergesort").groupby("pattern_id")["stop_id"]
    dup_patterns = int((g.size() != g.nunique()).sum())
    closures = int((g.first() == g.last()).sum())
    obs = {"fk_dangling": len(dangling), "tagged_dup_patterns": dup_patterns,
           "tagged_closure_patterns": closures}
    exp = {"fk_dangling": 0, "tagged_dup_patterns": _PS_DUP["dup_patterns"],
           "tagged_closure_patterns": _PS_DUP["closures"]}
    r = core.check_true("C-S01-B-008", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §5", positive_control=True,
                        note="시퀀스 내 stop 중복 55패턴이 사라져도(0건) FAIL — 검증기의 눈")
    _dump(r, vdir, pattern_stops[pattern_stops["stop_id"].isin(dangling)])
    return r


def c009_duplicates(dup: pd.DataFrame, vt: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S01-B-009: 노선 간 자(字)까지 동일 패턴 정확히 4건 (22↔977 ×2, 941↔948, 527↔537↔808).

    variant_tags role=duplicate 2행(route 323 내부 중복)과는 별개 사실 — 겹침 0 확인.
    """
    groups = sorted("|".join(sorted(str(rt).split("|"))) for rt in dup["routes"])
    vt_dup_routes = sorted(vt.loc[vt["role"] == "duplicate", "route"].unique())
    csv_routes = {r for g in groups for r in g.split("|")}
    obs = {"rows": int(len(dup)), "route_groups": groups,
           "vt_duplicate_routes": vt_dup_routes,
           "overlap_with_vt_duplicate": sorted(csv_routes & set(vt_dup_routes))}
    exp = {"rows": len(_DUP_ROUTE_GROUPS), "route_groups": sorted(_DUP_ROUTE_GROUPS),
           "vt_duplicate_routes": _VT_DUP_ROUTES, "overlap_with_vt_duplicate": []}
    r = core.check_true("C-S01-B-009", "CONTRACT", obs == exp, obs, exp,
                        "schedule_before.md §6; variant_tags.md §2",
                        note="노드 제거 금지 — s04는 쌍에 is_duplicate_pair 플래그만 단다")
    _dump(r, vdir, dup)
    return r


def c010_catalog(catalog: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S01-B-010: catalog 191 = schedule base 190 + 김해공항; route_class expect_count.

    base 레벨 has_schedule=False는 김해공항 1건뿐 — '50(내고산 방면)'은 base 50에 흡수.
    """
    hs = catalog["has_schedule"].astype(bool)
    obs = {"rows": int(len(catalog)),
           "class_dist": {k: int(v) for k, v in
                          sorted(catalog["route_class"].value_counts().items())},
           "general_incl_ulju": int(catalog["route_class"].isin(["general", "ulju"]).sum()),
           "no_schedule": sorted(catalog.loc[~hs, "route"].tolist())}
    exp = {"rows": _CATALOG["rows"], "class_dist": dict(sorted(_CLASS_DIST.items())),
           "general_incl_ulju": _CLASS_DIST["general"] + _CLASS_DIST["ulju"],
           "no_schedule": _CATALOG["no_schedule"]}
    r = core.check_true("C-S01-B-010", "CONTRACT", obs == exp, obs, exp,
                        "routes_stops.md §4")
    _dump(r, vdir, catalog[~hs])
    return r


def c011_is_loop_pc(patterns: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S01-B-011 [PC]: is_loop 단독 circular 판정 금지 확인 — circular인데 is_loop=False 3,
    main인데 is_loop=True 11이 실재해야 한다."""
    tagged = patterns[patterns["role"] != "support"]
    loop = tagged["is_loop"].astype(bool)
    obs = {"circular_not_loop": int(((tagged["role"] == "circular") & ~loop).sum()),
           "main_loop": int(((tagged["role"] == "main") & loop).sum())}
    exp = dict(_IS_LOOP_X)
    r = core.check_true("C-S01-B-011", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §2", positive_control=True,
                        note="is_loop만으로 circular 판정 불가 — 예외가 사라져도 FAIL")
    _dump(r, vdir, tagged[((tagged["role"] == "circular") & ~loop)
                          | ((tagged["role"] == "main") & loop)])
    return r


# ── PHYSICAL (WARN + --ack) ──────────────────────────────────────────────────
def p001_adjacent_gap(patterns: pd.DataFrame, pattern_stops: pd.DataFrame,
                      stops: pd.DataFrame, params: dict, vdir=None) -> core.CheckResult:
    """P-S01-B-001: canonical 시퀀스 인접 stop 간 거리 ≤ params.adjacent_gap_max_m.

    미실측 임계(감사 실측 아님) — WARN. 임계는 첫 실행 캘리브레이션으로 20,000m 확정
    (ADR-007: 실측 max 19,441m — 급행 고속도로·교외 국도·울주 DRT 순회 시퀀스 전부 개연,
    초기 제안 3,000m은 실세계 예외 91세그로 기각). 위반 = 조인/시퀀스 결함 수준의 도약 신호.
    params 미등재 시 SKIP하되 측정값을 기록한다(캘리브레이션 입력).
    """
    canon_ids = set(patterns.loc[patterns["in_canonical"].astype(bool), "pattern_id"])
    seg = (pattern_stops[pattern_stops["pattern_id"].isin(canon_ids)]
           .sort_values(["pattern_id", "seq"], kind="mergesort")
           .merge(stops[["stop_id", "lat", "lon"]], on="stop_id", how="left")
           .reset_index(drop=True))
    lat, lon = seg["lat"].values, seg["lon"].values
    d_all = np.atleast_1d(normalize.haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:]))
    same = (seg["pattern_id"] == seg["pattern_id"].shift(-1)).values[:-1]
    d = d_all[same]
    max_gap = float(np.max(d)) if len(d) else 0.0

    thr = params.get("adjacent_gap_max_m")
    if thr is None:
        return core.CheckResult(
            check_id="P-S01-B-001", check_class="PHYSICAL", severity="WARN", status="SKIP",
            observed={"max_gap_m": round(max_gap, 1), "segments": int(len(d))},
            expected="params.stages.s01_canonical.adjacent_gap_max_m 등재 필요 (ADR-007: 20,000m)",
            source="verification.md §5.3 (미실측 임계 — 캘리브레이션 대기)",
            failure_means=["param_sensitivity"],
            note="params 미등재 — 측정값만 기록하고 SKIP(요약의 skip 카운트에 노출). "
                 "임계 등재 시 활성화된다 (ADR-007)")
    over = int((d > thr).sum())
    r = core.check_true("P-S01-B-001", "PHYSICAL", over == 0,
                        {"over_threshold_segments": over, "max_gap_m": round(max_gap, 1),
                         "threshold_m": thr},
                        {"over_threshold_segments": 0},
                        "verification.md §5.3 (미실측 임계, ADR-007 캘리브레이션)",
                        failure_means=["logic_bug", "param_sensitivity"],
                        note="임계 20,000m은 실측 max 19,441m 기반 (ADR-007) — 위반 시 조인 불일치 "
                             "우선 의심, 실세계 예외면 재캘리브레이션 후 --ack")
    if r.failed and vdir is not None:
        _dump(r, vdir, seg.iloc[np.where(same & (d_all > thr))[0]])
    return r


def p002_backbone_coverage(patterns: pd.DataFrame, backbone: pd.DataFrame,
                           vdir=None, exp_covered: int = _BACKBONE_ROUTES_COVERED,
                           exp_uncovered: list | None = None,
                           exp_total: int = _ROUTES_TAGGED) -> core.CheckResult:
    """P-S01-B-002: backbone(main ∪ base 없는 circular)의 route 커버리지 고정 대조.

    design.md §5 s01 ⑥의 '184 전 커버' 가설은 최초 실행이 반증했다(ADR-006): 미커버
    34 route는 정확히 circular 자기참조 34건 보유 노선 — canonical 379 검증 규칙의 논리적 귀결.
    따라서 기대를 실측 검증 규칙(150/184 + 미커버 목록 exact)으로 고정하고 '변화'를 감시한다.
    커버리지가 어느 방향이든 움직이면 FAIL(수동 분류표 재태깅/로직 회귀 신호).
    """
    if exp_uncovered is None:
        exp_uncovered = _BACKBONE_UNCOVERED_ROUTES
    tagged = patterns[patterns["role"] != "support"]
    bb = patterns[patterns["in_backbone"].astype(bool)]
    covered = set(bb["route"])
    uncovered = sorted(set(tagged["route"]) - covered)
    obs = {"backbone_patterns": int(len(bb)),
           "backbone_stop_patterns": int(backbone["pattern_id"].nunique()),
           "routes_covered": len(covered), "routes_total": int(tagged["route"].nunique()),
           "routes_uncovered": uncovered}
    exp = {"backbone_patterns": 253, "backbone_stop_patterns": 253,
           "routes_covered": exp_covered, "routes_total": exp_total,
           "routes_uncovered": sorted(exp_uncovered)}
    r = core.check_true("P-S01-B-002", "PHYSICAL", obs == exp, obs, exp,
                        "ADR-006 실측 (design.md §5 s01 ⑥ 가설 반증 — 감사 미실측이므로 WARN 유지)",
                        failure_means=["upstream_regression", "logic_bug"],
                        note="하류 주의(caveat): s03/s04/s06의 backbone 입력에는 이 34 route가 "
                             "없다 — 순수 순환 노선의 골간 취급은 ADR-006이 s03/s04 설계로 이관. "
                             "BLOCK 승급은 사용자 리뷰 대기")
    _dump(r, vdir, tagged[tagged["route"].isin(set(uncovered) ^ set(exp_uncovered))]
          .drop_duplicates("route")[["pattern_id", "route", "role", "base_pattern_id_raw"]])
    return r


def p003_evidence_elementwise(patterns: pd.DataFrame, pattern_stops: pd.DataFrame,
                              ev_ids: dict, vdir=None) -> core.CheckResult:
    """P-S01-B-003: 결정(비drt) 태깅 패턴 대표 시퀀스와 evidence stop_ids의 요소 단위 일치.

    감사는 n_stops 일치까지만 실측 — 최초 WARN, 전행 일치 확인 후 BLOCK 게시 후보.
    """
    rep = _reps(pattern_stops)
    nondrt = patterns[(~patterns["is_drt"].astype(bool)) & (patterns["role"] != "support")]
    bad = [pid for pid in nondrt["pattern_id"]
           if pid in ev_ids and tuple(ev_ids[pid]) != rep.get(pid)]
    obs = {"checked": int(len(nondrt)), "element_mismatch": len(bad)}
    exp = {"checked": _TAGGED_PATTERNS - _DRT_PATTERNS, "element_mismatch": 0}
    r = core.check_true("P-S01-B-003", "PHYSICAL", obs == exp, obs, exp,
                        "variant_tags.md §5 (n_stops까지 실측 — 요소 단위는 미실측)",
                        failure_means=["logic_bug", "upstream_regression"],
                        note="전행 일치 확인 시 BLOCK 게시 근거가 된다")
    _dump(r, vdir, nondrt[nondrt["pattern_id"].isin(bad)])
    return r


# ── DIFF (SIGNAL) ────────────────────────────────────────────────────────────
def d002_general_express(catalog: pd.DataFrame) -> core.CheckResult:
    """D-S01-B-002: General 170(=general 160+울주 10) / Express 14 — 기준값 비교."""
    reference_values = diff.load_reference_values()
    exp = {"general": diff.reference_value(reference_values, "before.general_routes"),
           "express": diff.reference_value(reference_values, "before.express_routes")}
    obs = {"general": int(catalog["route_class"].isin(["general", "ulju"]).sum()),
           "express": int((catalog["route_class"] == "express").sum())}
    if obs == exp:
        status, note = "MATCH", ""
    else:
        documented = next((item for item in diff.load_known_deviations()
                           if item.check == "D-S01-B-002"), None)
        if documented is not None and obs == documented.measured:
            status, note = "EXPLAINED", f"{documented.id} ({documented.status}) — {documented.doc}"
        else:
            status, note = "UNEXPLAINED", "대장 미등재 편차"
    r = core.CheckResult(
        check_id="D-S01-B-002", check_class="DIFF", severity="SIGNAL", status=status,
        observed=obs, expected=exp, source="prior_baseline; routes_stops.md §4",
        failure_means=["convention_mismatch", "baseline_stale", "logic_bug"], note=note)
    if status == "UNEXPLAINED":
        note_path = diff.make_investigation_note(
            "s01-before-general-express", "D-S01-B-002", obs, exp)
        r.action_hint = f"조사 메모: {note_path}"
    return r


def run(ctx) -> list:
    patterns = ctx.df("patterns.parquet")
    pattern_stops = ctx.df("pattern_stops.parquet")
    disposition = ctx.df("pattern_disposition.parquet")
    canonical = ctx.df("canonical_rows.parquet")
    backbone = ctx.df("backbone_stops.parquet")
    ta = ctx.df("trip_attribution.parquet")
    catalog = ctx.df("route_catalog.parquet")
    dup = ctx.df("duplicates.csv")
    st = ctx.input_df("s00_ingest", "stop_times.parquet")
    vt = ctx.input_df("s00_ingest", "variant_tags.parquet")
    stops = ctx.input_df("s00_ingest", "stops.parquet")
    evidence = raw_variant_tags.load_evidence()
    ev_ids = {v["route_id"]: list(v["stop_ids"])
              for doc in evidence.values() for v in doc["variants"]}
    canonical_roles = ctx.params["canonical_roles"]
    v = ctx.vdir

    role_trips = ta["role"].value_counts()
    attributed = float(ta["trip_id"].nunique()) / float(st["trip_id"].nunique())

    results = [
        c001_pattern_reconstruction(patterns, pattern_stops, st, ev_ids, vt, v),
        c002_join_direction(patterns, vt, ta, v),
        c003_base_scope(patterns, v),
        c004_canonical_formula(patterns, canonical, canonical_roles, v),
        c005_disposition_accounting(disposition, v),
        c006_attribution_accounting(patterns, ta, v),
        c007_route_coverage(patterns, v),
        c008_pattern_stops(pattern_stops, stops, vt, v),
        c009_duplicates(dup, vt, v),
        c010_catalog(catalog, v),
        c011_is_loop_pc(patterns, v),
        p001_adjacent_gap(patterns, pattern_stops, stops, ctx.params, v),
        p002_backbone_coverage(patterns, backbone, v),
        p003_evidence_elementwise(patterns, pattern_stops, ev_ids, v),
        diff.judge("D-S01-B-001", int(len(canonical)), "before.canonical.rows",
                   metric="s01-before-canonical-rows"),
        d002_general_express(catalog),
        diff.judge("D-S01-B-003", int(role_trips.get("main", 0)),
                   "before.canonical.trips.main", metric="s01-before-main-trips"),
        diff.judge("D-S01-B-004", int(role_trips.get("circular", 0)),
                   "before.canonical.trips.circular", metric="s01-before-circular-trips"),
        diff.judge("D-S01-B-005", int(role_trips.get("short_turn", 0)),
                   "before.canonical.trips.short_turn", metric="s01-before-short-turn-trips"),
        diff.judge("D-S01-B-006", int(role_trips.get("branch", 0)),
                   "before.canonical.trips.branch", metric="s01-before-branch-trips"),
        diff.judge("D-S01-B-007", attributed, "before.trip_attribution",
                   metric="s01-before-trip-attribution"),
    ]
    # KD-0004는 '방식 차이'의 기록 — 수치는 재현(1.0)되므로 MATCH가 정상이다
    results[-1].note = results[-1].note or (
        "ADR-001: role=support 신설 + parent_route 귀속으로 100% 재현 (KD-0004 방식 차이)")
    return results
