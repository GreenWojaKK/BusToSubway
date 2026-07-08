"""s02_place 필수 검증 공통 구현 — C-S02-*-001~008, P-S02-*-001, D-S02-B-001~003
(verification.md §5.5 + stage2_place_hub_spec.md §4.5).

001~005·007·008은 양 스코프 공통(scope 인자로 check_id 발번), 006·DIFF는 before 전용
(after는 감사 미실측·기준값 부재가 검증 규칙). 개별 체크 함수는 데이터프레임을 직접 받는다 —
위반 주입 테스트(tests/unit/test_s02_violations.py)가 단독 호출한다.

아래 상수는 감사 실측 기준값 또는 명세 §4.5가 고정한 술어 상수다(임계값 아님 — 튜닝 금지).
출처 약어: [RS]=reference/audit/routes_stops.md.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import paths
from checks import core, diff
from dataio import normalize
from s02_place import diagnostics, merge

# ── 감사 기준 상수 ────────────────────────────────────────────────────────────────
_STOPS_TOTAL = {"before": 3409, "after": 3224}   # [RS§1,§5] stop 우주 전량
# 이름당 stop 분포 — 감사 [RS§5]의 bus_route 이름 우주 {1:335, 2:1297, 3+:128}에서
# alias 사실('양우내안에' 표기가 stop 우주에 부재, '양우내안애' 2 stops)로 기계 유도:
# {335−2, 1297+1, 128} (stage2_place_hub_spec.md §4.5 정오 — 감사 실측+기계 유도라 BLOCK 유지)
_NAME_DIST_BEFORE = {"1": 333, "2": 1298, "3+": 128}
_RECOMPUTE_TOL_M = 0.5        # C-S02-*-008 재계산 대조 술어 상수 (spec §4.5 — round6 오차 상회)
_ID_PREFIX = {"before": "PB", "after": "PA"}
_SRC = "routes_stops.md §1,§5; stage2_place_hub_spec.md §4.5"


def _sc(scope: str) -> str:
    return {"before": "B", "after": "A"}[scope]


def _dump(r: core.CheckResult, vdir, df) -> None:
    """실패 표본 의무(verification.md §7 규율 6). 재검증 경로(게시본 불변)는 기존 경로 재사용."""
    if vdir is not None and r.failed and df is not None and len(df):
        try:
            r.sample_path = core.dump_sample(vdir, r.check_id, df)
        except paths.WriteViolation:
            existing = Path(vdir) / "_debug" / f"{r.check_id}_sample.csv"
            r.sample_path = str(existing) if existing.exists() else None


# ── CONTRACT ─────────────────────────────────────────────────────────────────
def c001_full_mapping(map_df: pd.DataFrame, stops: pd.DataFrame, scope: str,
                      vdir=None, expected_total: int | None = None) -> core.CheckResult:
    """C-S02-*-001: stop 전량 매핑 — 맵 행수 == stops 행수 == 감사 실측, stop_id 유일, 결측 0."""
    total = _STOPS_TOTAL[scope] if expected_total is None else int(expected_total)
    obs = {"map_rows": int(len(map_df)), "stops_rows": int(len(stops)),
           "stop_id_dup": int(map_df["stop_id"].duplicated().sum()),
           "null_rows": int(map_df[["stop_id", "place_id", "name_norm"]]
                            .isna().any(axis=1).sum())}
    exp = {"map_rows": total, "stops_rows": total, "stop_id_dup": 0, "null_rows": 0}
    r = core.check_true(f"C-S02-{_sc(scope)}-001", "CONTRACT", obs == exp, obs, exp, _SRC,
                        note="stop 소실 0 — place는 stop을 대체하지 않는다 (design.md §2.3)")
    missing = stops[~stops["stop_id"].isin(set(map_df["stop_id"]))]
    _dump(r, vdir, missing if len(missing) else map_df[map_df["stop_id"].duplicated(keep=False)])
    return r


def c002_no_cross_name(places: pd.DataFrame, map_df: pd.DataFrame, scope: str,
                       vdir=None) -> core.CheckResult:
    """C-S02-*-002: cross-name 자동 병합 0 (최우선 — 위반 = 병합 로직 회귀).

    is_override_merged==False인 place 전부에서 멤버 name_norm nunique==1
    AND n_names>1 → is_override_merged==True.
    """
    nun = map_df.groupby("place_id")["name_norm"].nunique()
    place_index = places.set_index("place_id")
    merged = place_index["is_override_merged"].astype(bool)
    auto_cross = sorted(pid for pid, k in nun.items()
                        if k > 1 and not bool(merged.get(pid, False)))
    unflagged = sorted(place_index.index[(place_index["n_names"].astype(int) > 1) & ~merged])
    obs = {"cross_name_auto_merged": len(auto_cross),
           "n_names_gt1_unflagged": len(unflagged)}
    exp = {"cross_name_auto_merged": 0, "n_names_gt1_unflagged": 0}
    r = core.check_true(f"C-S02-{_sc(scope)}-002", "CONTRACT", obs == exp, obs, exp,
                        "verification.md §5.5 (고정 규칙: 이름은 blocking key)",
                        note="위반 = cross-name union 경로 유입 — override(merge) 경유만 허용")
    _dump(r, vdir, map_df[map_df["place_id"].isin(set(auto_cross) | set(unflagged))])
    return r


def c003_place_id_determinism(places: pd.DataFrame, map_df: pd.DataFrame, scope: str,
                              vdir=None) -> core.CheckResult:
    """C-S02-*-003: 전 place에서 place_id == make_place_id(scope, name_norm, 멤버) 재계산 일치."""
    members = map_df.groupby("place_id")["stop_id"].agg(list)
    bad = []
    for row in places.itertuples():
        mem = members.get(row.place_id)
        if mem is None or merge.make_place_id(scope, row.name_norm, mem) != row.place_id:
            bad.append(row.place_id)
    obs = {"recompute_mismatch": len(bad), "checked": int(len(places))}
    exp = {"recompute_mismatch": 0, "checked": int(len(places))}
    r = core.check_true(f"C-S02-{_sc(scope)}-003", "CONTRACT", obs == exp, obs, exp,
                        "design.md §5 s02 (콘텐츠 파생 결정적 id)")
    _dump(r, vdir, places[places["place_id"].isin(bad)])
    return r


def c004_cluster_validity(places: pd.DataFrame, map_df: pd.DataFrame, stops: pd.DataFrame,
                          linkage_max_m: float, scope: str, vdir=None) -> core.CheckResult:
    """C-S02-*-004: 클러스터 유효성 (override 미개입 place 한정 — merge는 명시 판단 산출).

    ① 연결성: place 내 stop들이 ≤ linkage_max_m 엣지만으로 연결(single-linkage 재검증)
    ② 분리 정당성: 같은 name_norm의 place 쌍 간 최근접 stop 거리 > linkage_max_m.
    """
    thr = float(linkage_max_m)
    non_override_places = places[~places["is_override_merged"].astype(bool)]
    coords = diagnostics._coords_by_place(
        map_df[map_df["place_id"].isin(set(non_override_places["place_id"]))], stops)

    disconnected = []
    for pid, (la, lo) in coords.items():
        n = len(la)
        if n < 2:
            continue
        parent = list(range(n))
        d = np.atleast_2d(normalize.haversine_m(
            la[:, None], lo[:, None], la[None, :], lo[None, :]))
        for i, j in np.argwhere(np.triu(d <= thr, k=1)):
            ri, rj = merge._uf_find(parent, int(i)), merge._uf_find(parent, int(j))
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)
        if len({merge._uf_find(parent, i) for i in range(n)}) > 1:
            disconnected.append(pid)

    close_pairs = []
    for name, g in non_override_places.groupby("name_norm", sort=True):
        ids = sorted(g["place_id"].tolist())
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if diagnostics.min_gap_m(coords[ids[i]], coords[ids[j]]) <= thr:
                    close_pairs.append((name, ids[i], ids[j]))

    obs = {"disconnected_places": len(disconnected),
           "same_name_pairs_within_linkage": len(close_pairs),
           "checked_places": int(len(non_override_places))}
    exp = {"disconnected_places": 0, "same_name_pairs_within_linkage": 0,
           "checked_places": int(len(non_override_places))}
    r = core.check_true(f"C-S02-{_sc(scope)}-004", "CONTRACT", obs == exp, obs, exp,
                        "verification.md §5.5 (single-linkage ≤ linkage_max_m 재검증)")
    bad = places[places["place_id"].isin(
        set(disconnected) | {p for _, a, b in close_pairs for p in (a, b)})]
    _dump(r, vdir, bad)
    return r


def c005_override_accounting(applied: pd.DataFrame | None, overrides: pd.DataFrame,
                             places: pd.DataFrame, scope: str, vdir=None) -> core.CheckResult:
    """C-S02-*-005: override_applied 존재 + 행수 == override 데이터 행수 + action enum
    + result_place_id 해소(최종 places 소속 또는 후속 행이 참조하는 연쇄 중간 id).

    빈 override → 0행 vacuous PASS. 참조 불능 자체는 빌드가 ContractViolation으로 즉사한다
    (spec §4.2) — 본 체크는 산출 이력의 사후 무결 재확인이다.
    """
    if applied is None:
        r = core.check_true(f"C-S02-{_sc(scope)}-005", "CONTRACT", False,
                            {"applied_exists": False}, {"applied_exists": True}, _SRC)
        return r
    later_refs: list[set] = []
    acc: set = set()
    for _, row in applied.iloc[::-1].iterrows():        # 뒤에서부터 누적 → k행의 '후속 참조'
        later_refs.append(set(acc))
        acc |= {str(row["place_a"]), str(row["place_b"])}
    later_refs.reverse()
    place_ids = set(places["place_id"])
    unresolved = [str(row["result_place_id"]) for k, (_, row) in enumerate(applied.iterrows())
                  if str(row["result_place_id"]) not in place_ids
                  and str(row["result_place_id"]) not in later_refs[k]]
    bad_actions = sorted(set(applied["action"].astype(str)) - set(merge.OVERRIDE_ACTIONS)) \
        if len(applied) else []
    obs = {"applied_rows": int(len(applied)), "override_rows": int(len(overrides)),
           "schema": list(applied.columns), "bad_actions": bad_actions,
           "unresolved_result_ids": unresolved}
    exp = {"applied_rows": int(len(overrides)), "override_rows": int(len(overrides)),
           "schema": merge.APPLIED_COLUMNS, "bad_actions": [], "unresolved_result_ids": []}
    r = core.check_true(f"C-S02-{_sc(scope)}-005", "CONTRACT", obs == exp, obs, exp,
                        "stage2_place_hub_spec.md §4.2 (전건 회계 — 조용한 불일치 방지)")
    _dump(r, vdir, applied[applied["result_place_id"].astype(str).isin(unresolved)])
    return r


def c006_name_stop_distribution(map_df: pd.DataFrame, vdir=None,
                                expected: dict | None = None) -> core.CheckResult:
    """C-S02-B-006 (before 전용): 이름당 stop 분포 == {1:333, 2:1298, 3+:128} exact.

    감사 [RS§5] 실측 {1:335, 2:1297, 3+:128}(bus_route 이름 우주)의 stop 우주 정오 —
    spec §4.5 주 (감사 실측 + alias 기계 유도이므로 BLOCK 유지).
    """
    exp_dist = _NAME_DIST_BEFORE if expected is None else expected
    counts = map_df.groupby("name_norm")["stop_id"].size()
    obs = {"1": int((counts == 1).sum()), "2": int((counts == 2).sum()),
           "3+": int((counts >= 3).sum())}
    r = core.check_true("C-S02-B-006", "CONTRACT", obs == dict(exp_dist),
                        obs, dict(exp_dist),
                        "routes_stops.md §5 + stage2_place_hub_spec.md §4.5 정오")
    return r


def c007_diag_structure(diag_um: pd.DataFrame | None, diag_al: pd.DataFrame | None,
                        places: pd.DataFrame, scope: str, vdir=None) -> core.CheckResult:
    """C-S02-*-007: 진단 2종 존재 + 스키마(§4.3) + diag_alias 두 name 상이 전행
    + diag_under_merge 두 place의 name_norm 동일(=행의 name_norm) 전행 + FK ⊆ places."""
    place_ids = set(places["place_id"])
    name_of = places.set_index("place_id")["name_norm"]
    obs: dict = {"under_merge_exists": diag_um is not None, "alias_exists": diag_al is not None}
    exp: dict = {"under_merge_exists": True, "alias_exists": True,
                 "um_schema_ok": True, "al_schema_ok": True,
                 "um_fk_dangling": 0, "al_fk_dangling": 0,
                 "um_name_mismatch_rows": 0, "al_same_name_rows": 0}
    bad_um = bad_al = None
    if diag_um is not None:
        obs["um_schema_ok"] = list(diag_um.columns) == diagnostics.UNDER_MERGE_COLUMNS
        fk = ~(diag_um["place_id_a"].isin(place_ids) & diag_um["place_id_b"].isin(place_ids))
        obs["um_fk_dangling"] = int(fk.sum())
        na = diag_um["place_id_a"].map(name_of)
        nb = diag_um["place_id_b"].map(name_of)
        mism = ~((na == nb) & (na == diag_um["name_norm"]))
        obs["um_name_mismatch_rows"] = int((mism & ~fk).sum())
        bad_um = diag_um[mism | fk]
    if diag_al is not None:
        obs["al_schema_ok"] = list(diag_al.columns) == diagnostics.ALIAS_COLUMNS
        fk = ~(diag_al["place_id_a"].isin(place_ids) & diag_al["place_id_b"].isin(place_ids))
        obs["al_fk_dangling"] = int(fk.sum())
        same = diag_al["name_a"] == diag_al["name_b"]
        obs["al_same_name_rows"] = int(same.sum())
        bad_al = diag_al[same | fk]
    ok = all(obs.get(k) == v for k, v in exp.items())
    r = core.check_true(f"C-S02-{_sc(scope)}-007", "CONTRACT", ok, obs, exp,
                        "stage2_place_hub_spec.md §4.3 (구조 검증 규칙)")
    sample = None
    if bad_um is not None and len(bad_um):
        sample = bad_um
    elif bad_al is not None and len(bad_al):
        sample = bad_al
    _dump(r, vdir, sample)
    return r


def c008_output_integrity(places: pd.DataFrame, map_df: pd.DataFrame, stops: pd.DataFrame,
                          scope: str, vdir=None) -> core.CheckResult:
    """C-S02-*-008: 산출 무결 — place_id regex·유일 / Σn_stops==len(stops) (accounting) /
    map FK ⊆ places / n_stops·n_names·span_m·centroid 재계산 대조(|Δ| < 0.5m — spec §4.5)
    / place_name == name_norm."""
    pattern = rf"{_ID_PREFIX[scope]}_[0-9a-f]{{8}}"
    regex_bad = int((~places["place_id"].astype(str).str.fullmatch(pattern)).sum())
    dup = int(places["place_id"].duplicated().sum())
    sum_n = int(places["n_stops"].astype(int).sum())
    dangling = sorted(set(map_df["place_id"]) - set(places["place_id"]))
    name_mismatch = int((places["place_name"] != places["name_norm"]).sum())

    mp = map_df[["stop_id", "place_id"]].merge(
        stops[["stop_id", "lat", "lon"]], on="stop_id", how="left")
    bad_geo, bad_counts = [], []
    grouped = dict(tuple(mp.groupby("place_id")))
    for row in places.itertuples():
        g = grouped.get(row.place_id)
        if g is None:
            bad_counts.append(row.place_id)
            continue
        la, lo = g["lat"].to_numpy(float), g["lon"].to_numpy(float)
        if int(row.n_stops) != len(g):
            bad_counts.append(row.place_id)
        c_gap = float(normalize.haversine_m(
            float(la.mean()), float(lo.mean()), float(row.lat_centroid), float(row.lon_centroid)))
        span = merge._max_pairwise_m(la, lo)
        if c_gap >= _RECOMPUTE_TOL_M or abs(span - float(row.span_m)) >= _RECOMPUTE_TOL_M:
            bad_geo.append(row.place_id)
    nn = map_df.groupby("place_id")["name_norm"].nunique()
    bad_nnames = sorted(r.place_id for r in places.itertuples()
                        if int(r.n_names) != int(nn.get(r.place_id, 0)))

    obs = {"regex_mismatch": regex_bad, "place_id_dup": dup,
           "sum_n_stops": sum_n, "stops_rows": int(len(stops)),
           "map_fk_dangling": len(dangling), "place_name_mismatch": name_mismatch,
           "n_stops_mismatch": len(bad_counts), "n_names_mismatch": len(bad_nnames),
           "geometry_recompute_mismatch": len(bad_geo)}
    exp = {"regex_mismatch": 0, "place_id_dup": 0,
           "sum_n_stops": int(len(stops)), "stops_rows": int(len(stops)),
           "map_fk_dangling": 0, "place_name_mismatch": 0,
           "n_stops_mismatch": 0, "n_names_mismatch": 0,
           "geometry_recompute_mismatch": 0}
    r = core.check_true(f"C-S02-{_sc(scope)}-008", "CONTRACT", obs == exp, obs, exp,
                        "stage2_place_hub_spec.md §4.4, §4.5 (구조 검증 규칙 — accounting 포함)")
    _dump(r, vdir, places[places["place_id"].isin(
        set(bad_geo) | set(bad_counts) | set(bad_nnames))])
    return r


# ── PHYSICAL (WARN + --ack) ──────────────────────────────────────────────────
def p001_span(places: pd.DataFrame, map_df: pd.DataFrame, stops: pd.DataFrame,
              params: dict, scope: str, vdir=None) -> core.CheckResult:
    """P-S02-*-001: 전 place span_m <= params.span_warn_m — 미실측 임계(WARN).

    [CAL] max 233.8m(before)/261.9m(after) — PASS 예상. FAIL 시 lineage·override 분포 표본.
    params 미등재 시 SKIP하되 측정값을 기록한다(캘리브레이션 입력 — s01 P-001 선례).
    """
    cid = f"P-S02-{_sc(scope)}-001"
    max_span = float(places["span_m"].max()) if len(places) else 0.0
    thr = params.get("span_warn_m")
    if thr is None:
        return core.CheckResult(
            check_id=cid, check_class="PHYSICAL", severity="WARN", status="SKIP",
            observed={"max_span_m": round(max_span, 1)},
            expected="params.stages.s02_place.span_warn_m 등재 필요",
            source="verification.md §5.5 (미실측 임계)",
            failure_means=["param_sensitivity"],
            note="params 미등재 — 측정값만 기록하고 SKIP")
    over = places[places["span_m"].astype(float) > float(thr)]
    r = core.check_true(cid, "PHYSICAL", len(over) == 0,
                        {"over_threshold_places": int(len(over)),
                         "max_span_m": round(max_span, 1), "threshold_m": float(thr)},
                        {"over_threshold_places": 0},
                        "verification.md §5.5 (미실측 임계 — WARN)",
                        failure_means=["logic_bug", "param_sensitivity"],
                        note="FAIL 시 병합 과확장/좌표 불일치 우선 확인 — 수용이면 --ack")
    if r.failed and vdir is not None and len(over):
        sample = over.copy()
        if "lineage" in stops.columns:
            lin = (map_df[map_df["place_id"].isin(set(over["place_id"]))]
                   .merge(stops[["stop_id", "lineage"]], on="stop_id", how="left")
                   .groupby("place_id")["lineage"]
                   .agg(lambda s: "|".join(f"{k}:{v}" for k, v in
                                           s.value_counts().sort_index().items())))
            sample["lineage_dist"] = sample["place_id"].map(lin)
        _dump(r, vdir, sample)
    return r


# ── DIFF (SIGNAL — before만) ─────────────────────────────────────────────────
def d001_place_total(places: pd.DataFrame, diag_um: pd.DataFrame | None,
                     params: dict) -> core.CheckResult:
    """D-S02-B-001: place 총수 vs baseline 1,759 — 초과분을 diag_under_merge와 자동 대조.

    obs − baseline == Σ(n_clusters_of_name − 1)이면 '분리 잉여가 전액 설명됨'을 note에
    기록한다(verification.md §5.5 action_hint 자동화 — 상한 null(전량 보고)일 때만 유효).
    """
    r = diff.judge("D-S02-B-001", int(len(places)), "before.place.total",
                   metric="place-total")
    if r.status != "MATCH" and diag_um is not None \
            and params.get("diag_under_merge_max_m") is None and len(diag_um):
        surplus = int(len(places)) - int(r.expected)
        per_name = diag_um.drop_duplicates("name_norm")["n_clusters_of_name"].astype(int) - 1
        s = int(per_name.sum())
        tag = "분리 잉여가 전액 설명됨" if surplus == s else "잉여 자동 대조 불일치"
        extra = (f"{tag}: obs-baseline={surplus}, Σ(n_clusters_of_name-1)={s} "
                 f"(diag_under_merge 자동 대조)")
        r.note = f"{r.note} | {extra}" if r.note else extra
        names = sorted(diag_um["name_norm"].astype(str).unique())
        r.action_hint = (f"{r.action_hint} | 같은 이름 분리 {len(names)}종 표본: "
                         f"{names[:10]}").strip(" |")
    return r


# ── 실행기 ──────────────────────────────────────────────────────────────────
def _try_df(ctx, name: str):
    try:
        return ctx.df(name)
    except FileNotFoundError:
        return None


def run_scope(ctx, scope: str) -> list:
    places = ctx.df("places.parquet")
    map_df = ctx.df("stop_place_map.parquet")
    stops = ctx.input_df("s00_ingest", "stops.parquet")
    diag_um = _try_df(ctx, "diag_under_merge.csv")
    diag_al = _try_df(ctx, "diag_alias.csv")
    applied = _try_df(ctx, "override_applied.csv")
    overrides = merge.load_overrides(scope)
    lm = float(ctx.params["linkage_max_m"])
    v = ctx.vdir

    results = [
        c001_full_mapping(map_df, stops, scope, v),
        c002_no_cross_name(places, map_df, scope, v),
        c003_place_id_determinism(places, map_df, scope, v),
        c004_cluster_validity(places, map_df, stops, lm, scope, v),
        c005_override_accounting(applied, overrides, places, scope, v),
    ]
    if scope == "before":
        results.append(c006_name_stop_distribution(map_df, v))
    results += [
        c007_diag_structure(diag_um, diag_al, places, scope, v),
        c008_output_integrity(places, map_df, stops, scope, v),
        p001_span(places, map_df, stops, ctx.params, scope, v),
    ]
    if scope == "before":
        results += [
            d001_place_total(places, diag_um, ctx.params),
            diff.judge("D-S02-B-002",
                       int(len(diag_um)) if diag_um is not None else -1,
                       "before.place.diag.under_merge_candidates", metric="place-total"),
            diff.judge("D-S02-B-003",
                       int(len(diag_al)) if diag_al is not None else -1,
                       "before.place.diag.alias_candidates", metric="place-total"),
        ]
    return results
