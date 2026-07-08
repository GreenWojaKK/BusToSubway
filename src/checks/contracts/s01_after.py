"""s01_canonical / after 필수 검증 — C-S01-A-001~009 (verification.md §5.4).

DIFF 없음 — after는 기준값 부재이며 크로스 스코프 비교 금지가 검증 규칙이다(design.md §7.1).
각 체크 함수는 DataFrame/경로를 인자로 받는 순수 함수다 — tests/unit의 위반 주입이
합성 데이터로 위음성을 검증할 수 있게 한다(verification.md §6). expected는 감사 실측
기준 상수가 기본값이고, 합성 테스트는 자기 데이터에 맞는 기대치를 주입한다.

사전 실측 주(2026-07-04, s00 v001 위 재현 — 구현 판별 근거):
  - trip 내 arr는 '감소 0 + 동시각 tie 정확히 4'다. spec §6.1의 'strict 단조' 표현과 달리
    감사 §6.4가 실측한 (OBE, arrival_time) 중복 4건(농소공영차고지 인접 stop 동시각)이
    trip 내부에 남는다 — 감사 실측이 상위 근거이므로 tie==4를 검증 규칙으로 고정한다.
  - is_partial은 '시작 seq > 전역 최소 관측 seq(=1)' 기준만이 감사 §5.5의 3개 수치
    (28.3% / 끝0 26.5%·끝1 44.9% / 2100 100%)를 전부 재현한다(trip_split docstring).
  - route_class 관측 분포는 yangsan 5(13·2100·2300·3000·3100 — 감사 §6.2 열거와 일치)다.
    첫 실행 확정치가 rules yaml expect_names로 게시됨(design.md §7.2) — 분포 기대치의
    기준은 yaml이고, 경성 항등(합계 184)은 감사 상수로 이중 고정한다.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import manifest
import paths
from checks import core
from dataio import raw_schedule_after
from s01_canonical import trip_split
from s01_canonical.after import get_rules

# ── 감사 실측 기준 상수 (원시 로그의 검증 규칙 — 튜닝 대상 아님. 출처를 각 체크 source에 기입) ──
N_EVENTS = 280_797               # [SA§1] 이벤트 전행 — trip_events 행수 보존
N_TRIPS = 4_524                  # [SA§5.1] 복원 trip 수
N_GROUPS = 1_055                 # [SA§5.1] (일자×OBE×pattern) 그룹 수
N_BOUNDARIES = 3_469             # [SA§5.1] seq 절단 경계 수 (3,469 + 1,055 == 4,524)
ARR_TIES_IN_TRIP = 4             # [SA§6.4] (OBE, arrival_time) 동시각 4건 — trip 내 tie로 잔존
PARTIAL_TRIPS = 1_279            # [SA§5.5] is_partial 실측 (28.3%)
PARTIAL_RATE = PARTIAL_TRIPS / N_TRIPS
PARTIAL_TOL = 0.02               # ±2%p — verification.md §5.4 C-S01-A-003
Y2100_NAME = "2100"              # [SA§5.2] 양산 광역 2100 — 경계 클리핑 100%
Y2100_TRIPS = 55                 # [SA§5.2] 11대 55 trips
GAP_MEDIAN_WINDOW_MIN = (30.0, 90.0)   # verification.md §5.4 C-S01-A-004 (실측 50.6 [SA§5.1])
MIDNIGHT_TRIPS = 37              # [SA§3] 자정 넘김 trip
MIDNIGHT_ROWS = 367              # [SA§3] 05-07 도착 행
N_PATTERNS = 351                 # [SA§1] pattern(route_id) 수
N_ROUTE_NAMES = 184              # [SA§1] route_name 수 — 클래스 합 경성 항등의 우변
NAMELESS_TRIPS = 30              # [SA§6.2] 이름미상 3 pattern의 trip 합계
# 클래스 분포 기대치는 코드 상수가 아니라 rules yaml의 expect_names를 기준으로 삼는다
# (design.md 원칙 7 '규칙은 데이터' + §7.2 '첫 실행 확정치를 expect로 게시' 이행 —
# 2026-07-04 게시: yangsan 5 · express 5 · rapid_candidate 17 · town 74 · general 83).
# 감사 정합 [SA§6.2]: 3자리 83 + 한글숫자 74 + 4자리 26(=1xxx 17 + 양산 4 + 급행 5)
# + 1~2자리 1(양산 13). 경성 항등은 클래스 합 == 184 (audit 상수 N_ROUTE_NAMES).

GK = trip_split.GROUP_KEYS
TK = trip_split.TRIP_KEYS
DAY_S = trip_split.SEC_PER_DAY
MIN_PER_S = 1.0 / 60.0


# ── C-S01-A-001: trip 절단 재현 — 그룹 1,055 / 경계 3,469 / 4,524 trips ──────
def c001_reconstruction(trips: pd.DataFrame, trip_events: pd.DataFrame,
                        exp: dict | None = None) -> core.CheckResult:
    """산출물에서 절단을 독립 재계산(재정렬 → shift → 절단)해 라벨까지 대조한다.

    원본 행 순서 의존 금지 [SA§5.1] — 재계산은 항상 arr_ts 재정렬에서 출발하므로
    빌드가 행 순서에 기대는 회귀는 라벨 불일치로 즉시 검출된다.
    """
    exp = exp or {"groups": N_GROUPS, "trips": N_TRIPS,
                  "boundaries": N_BOUNDARIES, "events": N_EVENTS}
    ev = (trip_events.sort_values(GK + ["arr_ts", "master_seq"], kind="mergesort")
                     .reset_index(drop=True))
    prev = ev.groupby(GK, sort=False)["master_seq"].shift()
    cut = prev.notna() & (ev["master_seq"] <= prev)
    recut_no = (prev.isna() | (ev["master_seq"] <= prev)) \
        .groupby([ev[k] for k in GK]).cumsum()
    obs = {
        "groups": int(ev.groupby(GK).ngroups),
        "trips": int(len(trips)),
        "boundaries": int(cut.sum()),
        "events": int(len(trip_events)),
        "recut_label_mismatch": int((recut_no.astype("int64")
                                     != ev["trip_no"].astype("int64")).sum()),
        "trip_uid_null": int(trips["trip_uid"].isna().sum()
                             + trip_events["trip_uid"].isna().sum()),
        "accounting_groups_plus_boundaries": int(ev.groupby(GK).ngroups + cut.sum()),
    }
    ok = (obs["groups"] == exp["groups"] and obs["trips"] == exp["trips"]
          and obs["boundaries"] == exp["boundaries"] and obs["events"] == exp["events"]
          and obs["recut_label_mismatch"] == 0 and obs["trip_uid_null"] == 0
          and obs["accounting_groups_plus_boundaries"] == obs["trips"])
    return core.check_true(
        "C-S01-A-001", "CONTRACT", ok, obs,
        {**exp, "recut_label_mismatch": 0, "trip_uid_null": 0,
         "accounting_groups_plus_boundaries": exp["trips"]},
        "audit/schedule_after.md §5.1",
        note="경계 3,469 + 그룹 1,055 == 4,524 전수 회계 — 조용한 trip 소실 차단. "
             "재계산 라벨 대조로 원본 행 순서 의존 회귀를 검출한다.")


# ── C-S01-A-002: trip 내 단조·trip 간 겹침 0·자연키 유일 ─────────────────────
def c002_monotonic_unique(trips: pd.DataFrame, trip_events: pd.DataFrame,
                          exp_arr_ties: int = ARR_TIES_IN_TRIP) -> core.CheckResult:
    """seq strict 단조(위반 0) / arr 감소 0 + 동시각 tie 정확히 exp_arr_ties / 겹침 0.

    spec §6.1은 'arr strict'라 썼으나 감사 §6.4 실측(농소공영차고지 인접 stop 동시각
    4건)이 trip 내 tie로 잔존한다 — 감사 실측이 상위 근거(사전 실측으로 재확인).

    교차 정렬 검증: 시간순 정렬에서 seq 단조를, seq순 정렬에서 arr 단조를 검사한다 —
    검사 축과 정렬 축이 같으면 정렬이 위반을 숨기는 자기충족 체크가 된다(위반 주입
    테스트가 실제로 이 결함을 검출했다).
    """
    ev_t = trip_events.sort_values(TK + ["arr_ts", "master_seq"], kind="mergesort")
    d_seq = ev_t.groupby(TK, sort=False)["master_seq"].diff().dropna()
    ev_s = trip_events.sort_values(TK + ["master_seq"], kind="mergesort")
    d_arr = ev_s.groupby(TK, sort=False)["arr_s"].diff().dropna()

    t = trips.sort_values(TK, kind="mergesort")
    prev_end = t.groupby(GK)["end_s"].shift()
    overlap = int(((t["start_s"].astype("int64") - prev_end) < 0).sum())

    obs = {
        "seq_nonstrict_rows": int((d_seq <= 0).sum()),
        "arr_decreasing_rows": int((d_arr < 0).sum()),
        "arr_tie_rows": int((d_arr == 0).sum()),
        "overlap_pairs": overlap,
        "natural_key_dup": int(trip_events.duplicated(TK + ["master_seq"]).sum()),
        "trip_uid_dup": int(trips["trip_uid"].duplicated().sum()),
    }
    exp = {"seq_nonstrict_rows": 0, "arr_decreasing_rows": 0,
           "arr_tie_rows": exp_arr_ties, "overlap_pairs": 0,
           "natural_key_dup": 0, "trip_uid_dup": 0}
    return core.check_true(
        "C-S01-A-002", "CONTRACT", obs == exp, obs, exp,
        "audit/schedule_after.md §5.1, §6.4",
        note="(일자,obe,pattern,trip_no,seq) 유일 [SA§6.4]; arr tie 4건은 감사 실측 예외의 "
             "박제(양성 대조 성격) — 0건이 되어도 data_drift 신호다.")


# ── C-S01-A-003 [PC]: is_partial 28.3% ± 2%p + 2100 양산 100% boundary_clip ──
def c003_partial_pc(trips: pd.DataFrame, exp_rate: float = PARTIAL_RATE,
                    tol: float = PARTIAL_TOL,
                    exp_y2100: int = Y2100_TRIPS) -> core.CheckResult:
    """양성 대조군 — 부분 trip이 사라져도(0%) FAIL(검증기 생존 확인)."""
    n = len(trips)
    n_partial = int(trips["is_partial"].sum())
    rate = n_partial / n if n else 0.0
    t21 = trips[trips["route_name"] == Y2100_NAME]
    obs = {
        "n_partial": n_partial, "n_trips": n, "rate": round(rate, 4),
        "y2100_trips": int(len(t21)),
        "y2100_partial": int(t21["is_partial"].sum()),
        "y2100_reason_boundary_clip": int((t21["partial_reason"] == "boundary_clip").sum()),
        "reason_enum_unknown": sorted(set(trips["partial_reason"])
                                      - set(trip_split.PARTIAL_REASONS)),
        "reason_none_mismatch": int(((trips["partial_reason"] == "none")
                                     != ~trips["is_partial"]).sum()),
    }
    ok = (abs(rate - exp_rate) <= tol
          and obs["y2100_trips"] == exp_y2100
          and obs["y2100_partial"] == obs["y2100_trips"]
          and obs["y2100_reason_boundary_clip"] == obs["y2100_trips"]
          and not obs["reason_enum_unknown"]
          and obs["reason_none_mismatch"] == 0)
    return core.check_true(
        "C-S01-A-003", "CONTRACT", ok, obs,
        {"rate": f"{exp_rate:.4f} ± {tol}", "y2100_trips": exp_y2100,
         "y2100_partial": exp_y2100, "y2100_reason_boundary_clip": exp_y2100,
         "reason_enum_unknown": [], "reason_none_mismatch": 0},
        "audit/schedule_after.md §5.5, §5.2", positive_control=True,
        note="기준 = 시작 seq > 전역 최소 관측 seq(=1) — 감사 3개 수치 전부 재현하는 유일한 "
             "규칙(사전 실측). 2100 양산은 경계 클리핑으로 100% partial·boundary_clip.")


# ── C-S01-A-004 [PC]: trip 간 gap 중앙값 ∈ [30, 90]분 ────────────────────────
def c004_gap_median(trips: pd.DataFrame,
                    window_min: tuple[float, float] = GAP_MEDIAN_WINDOW_MIN
                    ) -> core.CheckResult:
    """회차 layover를 절단으로 오인하지 않았는지 — 실측 50.6분 [SA§5.1]."""
    t = trips.sort_values(TK, kind="mergesort")
    prev_end = t.groupby(GK)["end_s"].shift()
    gap_min = ((t["start_s"].astype("float64") - prev_end) * MIN_PER_S).dropna()
    obs = {"n_gaps": int(len(gap_min)),
           "median_min": round(float(gap_min.median()), 1) if len(gap_min) else None,
           "p5_min": round(float(gap_min.quantile(0.05)), 1) if len(gap_min) else None,
           "p95_min": round(float(gap_min.quantile(0.95)), 1) if len(gap_min) else None}
    lo, hi = window_min
    ok = obs["median_min"] is not None and lo <= obs["median_min"] <= hi
    return core.check_true(
        "C-S01-A-004", "CONTRACT", ok, obs,
        {"median_min": f"[{lo}, {hi}]"},
        "audit/schedule_after.md §5.1 (실측 중앙값 50.6분)", positive_control=True,
        note="중앙값이 창을 벗어나면 절단 과잉(회차 layover 오절단) 또는 절단 누락 신호.")


# ── C-S01-A-005: crosses_midnight 37 trips (05-07 도착 367행 포함) ───────────
def c005_midnight(trips: pd.DataFrame, trip_events: pd.DataFrame,
                  exp_trips: int = MIDNIGHT_TRIPS,
                  exp_rows: int = MIDNIGHT_ROWS) -> core.CheckResult:
    over = trip_events["arr_s"].astype("int64") >= DAY_S
    recomputed = set(trip_events.loc[over, "trip_uid"])
    flagged = set(trips.loc[trips["crosses_midnight"], "trip_uid"])
    obs = {"rows_after_24h": int(over.sum()),
           "trips_crossing": len(recomputed),
           "flag_mismatch_trips": len(recomputed ^ flagged)}
    exp = {"rows_after_24h": exp_rows, "trips_crossing": exp_trips,
           "flag_mismatch_trips": 0}
    return core.check_true(
        "C-S01-A-005", "CONTRACT", obs == exp, obs, exp,
        "audit/schedule_after.md §3",
        note="자정 넘김은 timestamp 날짜부(05-07) 방식 — service_s 24h+ 연장으로 표현 "
             "(design.md §2.4). 운행일자는 20250506 유지가 원본 규약.")


# ── C-S01-A-006: pattern_registry 351 전 커버 + 이름미상 3 NULL 유지 ─────────
def c006_pattern_registry(pattern_registry: pd.DataFrame, trips: pd.DataFrame,
                          trip_events: pd.DataFrame,
                          exp_patterns: int = N_PATTERNS,
                          exp_nameless: frozenset = raw_schedule_after.NAMELESS_PATTERNS,
                          exp_nameless_trips: int = NAMELESS_TRIPS) -> core.CheckResult:
    reg_set = set(pattern_registry["pattern_id"])
    ev_set = set(trip_events["pattern_id"])
    nameless_obs = set(pattern_registry.loc[pattern_registry["route_name"].isna(),
                                            "pattern_id"])
    flag_mismatch = int((pattern_registry["route_name"].isna()
                         != pattern_registry["name_unknown"]).sum())
    obs = {
        "rows": int(len(pattern_registry)),
        "pattern_id_dup": int(pattern_registry["pattern_id"].duplicated().sum()),
        "coverage_symdiff": len(reg_set ^ ev_set),
        "nameless_patterns": sorted(nameless_obs),
        "name_unknown_flag_mismatch": flag_mismatch,
        "nameless_trips": int(trips["route_name"].isna().sum()),
    }
    exp = {"rows": exp_patterns, "pattern_id_dup": 0, "coverage_symdiff": 0,
           "nameless_patterns": sorted(exp_nameless),
           "name_unknown_flag_mismatch": 0, "nameless_trips": exp_nameless_trips}
    return core.check_true(
        "C-S01-A-006", "CONTRACT", obs == exp, obs, exp,
        "audit/schedule_after.md §1, §6.2",
        note="이름미상 3 pattern은 route_name=NULL 유지 — 이름은 id에서 유추 가능하나 "
             "미확정이므로 임의 명명 금지가 검증 규칙.")


# ── C-S01-A-007: 정차열 기준 = route_master 승계 (stop_sequence 미사용) ───────
def c007_route_master_inherited(out_path: Path, upstream_path: Path,
                                declared_sha: str | None,
                                route_master: pd.DataFrame | None = None,
                                route_agg: pd.DataFrame | None = None
                                ) -> core.CheckResult:
    """승계 무결성: s01 route_master == s00 원본(sha256 동일 — 재산출·변형 금지).

    dense-rank 대조는 참고 기록으로만(감사 '94% 일치' [SA§5.4]) — bus_route_after의
    stop_sequence는 재부여 번호라 정차열 원천으로 사용 금지가 검증 규칙이며, 이 수치가
    게이트가 되면 그 검증 규칙을 스스로 위반하게 된다.
    """
    out_sha = "sha256:" + manifest.sha256_file(Path(out_path))
    up_sha = "sha256:" + manifest.sha256_file(Path(upstream_path))
    obs: dict = {"inherited_sha_equal": out_sha == up_sha,
                 "declared_input_sha_equal": (declared_sha is None
                                              or out_sha == declared_sha)}
    if route_master is not None and route_agg is not None:
        rm = route_master.sort_values(["pattern_id", "master_seq"]).copy()
        rm["dense"] = rm.groupby("pattern_id").cumcount() + 1
        m = route_agg.merge(rm[["pattern_id", "dense", "stop_id"]],
                            left_on=["pattern_id", "seq"],
                            right_on=["pattern_id", "dense"],
                            how="left", suffixes=("", "_rm"))
        same = m["stop_id"] == m["stop_id_rm"]
        obs["dense_rank_row_match_ref"] = round(float(same.mean()), 4)
        pat_ok = same.groupby(m["pattern_id"]).all()
        obs["dense_rank_patterns_all_match_ref"] = f"{int(pat_ok.sum())}/{len(pat_ok)}"
    ok = obs["inherited_sha_equal"] and obs["declared_input_sha_equal"]
    return core.check_true(
        "C-S01-A-007", "CONTRACT", ok, obs,
        {"inherited_sha_equal": True, "declared_input_sha_equal": True},
        "audit/schedule_after.md §5.4",
        note="dense_rank_*_ref는 참고 기록(감사 94% — 게이트 아님). 정차열 기준은 "
             "route_master 격자이며 stop_sequence 사용 금지.")


# ── C-S01-A-008: route_class — 경성 항등(합 184) + 감사 형태 분포 ────────────
def c008_route_class(route_registry: pd.DataFrame, rules: dict,
                     exp_dist: dict | None = None,
                     exp_total: int = N_ROUTE_NAMES) -> core.CheckResult:
    # 분포 기대치 기준 = rules yaml의 expect_names (첫 실행 확정치 게시 — design.md §7.2).
    # 테스트는 exp_dist 주입으로 자기 데이터 기대치를 쓴다.
    if exp_dist is None:
        exp_dist = {r["class"]: int(r["expect_names"])
                    for r in rules.get("rules", []) if r.get("expect_names") is not None}
    counts = route_registry["route_class"].value_counts().to_dict()
    counts = {str(k): int(v) for k, v in counts.items()}
    # yaml expect_names 대조 — 기대치 노후 감지 기록(게시 후에는 공집합이 기대)
    stale = []
    for r in rules.get("rules", []):
        e = r.get("expect_names")
        if e is not None and counts.get(r["class"], 0) != e:
            stale.append({"class": r["class"], "expect_names": e,
                          "observed": counts.get(r["class"], 0)})
    obs = {
        "class_counts": counts,
        "total": int(len(route_registry)),
        "class_null_rows": int(route_registry["route_class"].isna().sum()),
        "rules_era": rules.get("era"),
        "portability": rules.get("portability"),
        "yaml_accounting_total": rules.get("accounting", {}).get("total_names"),
        "expect_names_stale": stale,
    }
    ok = (counts == exp_dist and obs["total"] == exp_total
          and sum(counts.values()) == exp_total
          and obs["class_null_rows"] == 0
          and obs["rules_era"] == "after" and obs["portability"] == "forbidden"
          and obs["yaml_accounting_total"] == exp_total)
    return core.check_true(
        "C-S01-A-008", "CONTRACT", ok, obs,
        {"class_counts": exp_dist, "total": exp_total, "class_null_rows": 0,
         "rules_era": "after", "portability": "forbidden",
         "yaml_accounting_total": exp_total},
        "audit/schedule_after.md §6.2, audit/routes_stops.md §4",
        note="경성 항등 = 클래스 합 == 184. 분포 기대치 기준은 rules yaml expect_names "
             "(첫 실행 확정치 게시 완료 — yangsan 5·express 5·rapid 17·town 74·general 83; "
             "감사 형태 분포와 정합). expect_names_stale은 게시 후 공집합이 기대. "
             "before 규칙 이식은 get_rules era 검증이 KeyError로 차단(portability 검증 규칙).")


# ── C-S01-A-009: main 제안 — 이중 규칙 합의·자동 판정 0 (ADR-003) ────────────
def c009_main_proposal(proposal: pd.DataFrame,
                       route_registry: pd.DataFrame) -> core.CheckResult:
    p = proposal.copy()
    # CSV 경유 로드는 dtype=str — 불리언 정규화
    needs = p["needs_review"].astype(str).str.lower() == "true"
    cand = p["main_candidate"].fillna("").astype(str)
    vc0 = p["pattern_id_vc0"].fillna("").astype(str)
    top = p["pattern_id_top"].fillna("").astype(str)

    agree_rows = ~needs
    basis_counts = p["basis"].value_counts().to_dict()
    obs = {
        "rows": int(len(p)),
        "route_name_dup": int(p["route_name"].duplicated().sum()),
        "coverage_symdiff": len(set(p["route_name"])
                                ^ set(route_registry["route_name"])),
        "forced_calls": int((needs & (cand != "")).sum()),
        "agree_without_candidate": int((agree_rows & (cand == "")).sum()),
        "agree_candidate_mismatch": int((agree_rows
                                         & ((cand != vc0) | (cand != top))).sum()),
        "basis_counts": {str(k): int(v) for k, v in basis_counts.items()},
        "agreement_rate": round(float(agree_rows.mean()), 4) if len(p) else None,
    }
    ok = (obs["route_name_dup"] == 0 and obs["coverage_symdiff"] == 0
          and obs["forced_calls"] == 0 and obs["agree_without_candidate"] == 0
          and obs["agree_candidate_mismatch"] == 0)
    return core.check_true(
        "C-S01-A-009", "CONTRACT", ok, obs,
        {"route_name_dup": 0, "coverage_symdiff": 0, "forced_calls": 0,
         "agree_without_candidate": 0, "agree_candidate_mismatch": 0},
        "ADR-003 (design.md §10)",
        note="합의(variant_code==0 ∧ 최다 trip)만 main_candidate 확정 — 불일치는 "
             "needs_review=True 보존(자동 판정하지 않음). agreement_rate·basis_counts는 "
             "향후 ADR-003 확정의 근거 리포트.")


# ── 실패 표본 덤프 (운영 규율 6: 덤프 없는 FAIL은 하네스 결함) ────────────────
def _violation_frame(cid: str, trips, trip_events, preg, proposal):
    if cid == "C-S01-A-001":
        ev = (trip_events.sort_values(GK + ["arr_ts", "master_seq"], kind="mergesort")
                         .reset_index(drop=True))
        prev = ev.groupby(GK, sort=False)["master_seq"].shift()
        recut_no = (prev.isna() | (ev["master_seq"] <= prev)) \
            .groupby([ev[k] for k in GK]).cumsum()
        return ev[recut_no.astype("int64") != ev["trip_no"].astype("int64")]
    if cid == "C-S01-A-002":
        ev_t = trip_events.sort_values(TK + ["arr_ts", "master_seq"], kind="mergesort")
        bad_seq = (ev_t.groupby(TK, sort=False)["master_seq"].diff() <= 0).fillna(False)
        ev_s = trip_events.sort_values(TK + ["master_seq"], kind="mergesort")
        bad_arr = (ev_s.groupby(TK, sort=False)["arr_s"].diff() < 0).fillna(False)
        dup = ev_s.duplicated(TK + ["master_seq"], keep=False)
        return pd.concat([ev_t[bad_seq], ev_s[bad_arr | dup]]).drop_duplicates()
    if cid == "C-S01-A-003":
        t21 = trips[trips["route_name"] == Y2100_NAME]
        return t21[~t21["is_partial"] | (t21["partial_reason"] != "boundary_clip")]
    if cid == "C-S01-A-004":
        t = trips.sort_values(TK, kind="mergesort").copy()
        t["gap_min"] = ((t["start_s"].astype("float64")
                         - t.groupby(GK)["end_s"].shift()) * MIN_PER_S)
        return t[t["gap_min"].notna()]
    if cid == "C-S01-A-005":
        over_uid = set(trip_events.loc[
            trip_events["arr_s"].astype("int64") >= DAY_S, "trip_uid"])
        return trips[trips["crosses_midnight"] != trips["trip_uid"].isin(over_uid)]
    if cid == "C-S01-A-006":
        return preg[preg["route_name"].isna() != preg["name_unknown"]]
    if cid == "C-S01-A-009":
        needs = proposal["needs_review"].astype(str).str.lower() == "true"
        cand = proposal["main_candidate"].fillna("").astype(str)
        return proposal[(needs & (cand != "")) | (~needs & (cand == ""))]
    return None


def run(ctx) -> list[core.CheckResult]:
    """체크 실행 진입점 — 러너 규약 run(ctx) -> list[CheckResult]."""
    trips = ctx.df("trips.parquet")
    trip_events = ctx.df("trip_events.parquet")
    preg = ctx.df("pattern_registry.parquet")
    rreg = ctx.df("route_registry.parquet")
    proposal = ctx.df("main_proposal.csv")
    route_master = ctx.df("route_master.parquet")
    route_agg = ctx.input_df("s00_ingest", "route_agg.parquet")
    rules = get_rules(ctx.scope)                   # era 명시 조회 — scope 미지정 금지

    up = ctx.inputs.artifacts[f"s00_ingest/{ctx.scope}"]
    up_rm = paths.artifact_dir("s00_ingest", ctx.scope,
                               up["version"]) / "route_master.parquet"
    declared = up["files"].get("route_master.parquet")

    results = [
        c001_reconstruction(trips, trip_events),
        c002_monotonic_unique(trips, trip_events),
        c003_partial_pc(trips),
        c004_gap_median(trips),
        c005_midnight(trips, trip_events),
        c006_pattern_registry(preg, trips, trip_events),
        c007_route_master_inherited(ctx.vdir / "route_master.parquet", up_rm,
                                    declared, route_master, route_agg),
        c008_route_class(rreg, rules),
        c009_main_proposal(proposal, rreg),
    ]
    for r in results:
        if r.failed and r.sample_path is None:
            bad = _violation_frame(r.check_id, trips, trip_events, preg, proposal)
            if bad is not None and len(bad):
                r.sample_path = core.dump_sample(ctx.vdir, r.check_id, bad)
    return results
