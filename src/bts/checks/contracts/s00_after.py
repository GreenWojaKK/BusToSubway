"""s00_ingest / after 필수 검증 — C-S00-A-000~011 + P-S00-A-001 (verification.md §5.2).

감사 로더 불변식(audit/schedule_after.md §8, audit/routes_stops.md §9의 after분)의
전건 CONTRACT 이식 + 양성 대조군(C-S00-A-004). DIFF 없음 — after는 기준값 부재이며
크로스 스코프 비교 금지가 검증 규칙이다(design.md §7.1).

raw 대조가 필요한 체크(A-001~004, A-011)는 bts.io 로더를 경유해 raw를 읽는다(io 관문 규약).
각 체크 함수는 DataFrame/Series를 인자로 받는 순수 함수다 — tests/unit의 위반 주입이
합성 데이터로 위음성을 검증할 수 있게 한다(verification.md §6).
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import bts.manifest as manifest
import bts.paths as paths
from bts.checks import core
from bts.io import raw_bus_route_after, raw_schedule_after, timeparse

# ── 감사 실측 expected (verification.md §5.2 — 출처를 각 체크 source에 기입) ──
RAW_FILES = (                        # C-S00-A-000 대상 raw 3종
    "data/ulsan_route_schedule_after.parquet",
    "data/ulsan_bus_route_after.parquet",
    "data/ulsan_stops_after.parquet",
)
ARR_FIRST_TS = "2025-05-06 오전 4:00:08"   # [SA§2] 실측 최초 도착
ARR_LAST_TS = "2025-05-07 오전 1:10:01"    # [SA§2] 실측 최종 도착 (자정 넘김 표현)
AGG_MATCH_CELLS = 17_840             # [SA§6.3] 도착횟수 셀 대조 17,840/17,841 완전 일치
ZONE_INFO = "양산시"                  # [SA§1] zone 양산시 16행 — 정보 기록용


def _svc_date():
    return datetime.strptime(raw_schedule_after.SERVICE_DATE, "%Y%m%d").date()


def _window() -> tuple[int, int]:
    t = paths.load_params()["time"]
    return (t["service_min_h"] * timeparse.SEC_PER_HOUR,
            t["service_max_h"] * timeparse.SEC_PER_HOUR)


# ── C-S00-A-000: raw sha256 == 기준값 (모든 실행의 0번 체크) ─────────────────
def c000_raw_hashes() -> core.CheckResult:
    import yaml
    with open(paths.BASELINE_DIR / "raw_hashes.yaml", encoding="utf-8") as f:
        frozen = yaml.safe_load(f)["files"]
    obs = {}
    for rel in RAW_FILES:
        p = paths.ROOT / rel
        if rel not in frozen:
            obs[rel] = "기준값 부재"
        elif not p.exists():
            obs[rel] = "파일 부재"
        else:
            obs[rel] = "ok" if manifest.sha256_file(p) == frozen[rel] else "MISMATCH"
    ok = all(v == "ok" for v in obs.values())
    return core.check_true(
        "C-S00-A-000", "CONTRACT", ok, obs, {rel: "ok" for rel in RAW_FILES},
        "reference/prior_baseline/raw_hashes.yaml",
        failure_means=["data_drift"],
        note="불일치 = data_drift(사람 에스컬레이션 — 클린룸 위반 가능성). "
             "일치 상태의 로더 검증 실패 = loader_bug 로 기계 분리된다.")


# ── C-S00-A-001: raw shape·첫 컬럼명·완전 중복 0 ─────────────────────────────
def c001_raw_shape(sched_raw: pd.DataFrame) -> core.CheckResult:
    obs = {"shape": list(sched_raw.shape),
           "first_col": str(sched_raw.columns[0]),
           "full_dup_rows": int(sched_raw.duplicated().sum())}
    exp = {"shape": [raw_schedule_after.ROWS, len(raw_schedule_after.COLUMNS)],
           "first_col": raw_schedule_after.COLUMNS[0],
           "full_dup_rows": 0}
    return core.check_true("C-S00-A-001", "CONTRACT", obs == exp, obs, exp,
                           "audit/schedule_after.md §1",
                           note="첫 컬럼명은 공백 포함 '운행 일자' — BOM 포함 여부 확인")


# ── C-S00-A-002: 운행일자 nunique==1, 값 20250506 ────────────────────────────
def c002_service_date(dates: pd.Series) -> core.CheckResult:
    obs = {"nunique": int(dates.nunique()), "value": str(dates.iloc[0])}
    exp = {"nunique": 1, "value": raw_schedule_after.SERVICE_DATE}
    return core.check_true("C-S00-A-002", "CONTRACT", obs == exp, obs, exp,
                           "audit/schedule_after.md §2",
                           note="after는 평일(화) 1일 표본 — 시간층 한계 명시의 근거")


# ── C-S00-A-003: arrival 전행 오전/오후 regex ────────────────────────────────
def c003_arrival_format(arrival: pd.Series) -> core.CheckResult:
    return core.regex_all("C-S00-A-003", "CONTRACT", arrival, timeparse.KR_TS_RE.pattern,
                          "audit/schedule_after.md §3",
                          note="오전12=0시, 오후12=12시 — 파서는 bts.io.timeparse.parse_kr_ampm")


# ── C-S00-A-004 [PC]: departure sentinel 정확히 8,302 + 1, 그 외 이형 0 ──────
def c004_dep_sentinel(dep_raw: pd.Series, n_flagged: int) -> core.CheckResult:
    """양성 대조군 — sentinel이 사라져도(0건) FAIL(검증기 생존 확인)."""
    nonre = dep_raw[~dep_raw.str.fullmatch(timeparse.KR_TS_RE.pattern)]
    obs_counts = nonre.value_counts().to_dict()
    exp_counts = dict(raw_schedule_after.SENTINEL_COUNTS)
    exp_flagged = sum(exp_counts.values())
    ok = (obs_counts == exp_counts) and (n_flagged == exp_flagged)
    return core.check_true(
        "C-S00-A-004", "CONTRACT", ok,
        {"raw_sentinel_counts": {str(k): int(v) for k, v in obs_counts.items()},
         "events_dep_is_sentinel_rows": int(n_flagged)},
        {"raw_sentinel_counts": exp_counts, "events_dep_is_sentinel_rows": exp_flagged},
        "audit/schedule_after.md §4", positive_control=True,
        note="숨은 결측의 플래그화 검증 — 이벤트 시각 기준은 arrival(결측 0)")


# ── C-S00-A-005: dep≥arr, arr 실측 범위, service_s 창 ────────────────────────
def c005_time_bounds(ev: pd.DataFrame) -> core.CheckResult:
    lo, hi = _window()
    svc = _svc_date()
    arr_lo = timeparse.to_service_s(timeparse.parse_kr_ampm(ARR_FIRST_TS), svc)
    arr_hi = timeparse.to_service_s(timeparse.parse_kr_ampm(ARR_LAST_TS), svc)
    arr = ev["arr_s"]
    dep = ev["dep_s"]
    valid = dep.notna()
    obs = {
        "dep_lt_arr_rows": int((dep[valid].astype("int64")
                                < arr[valid].astype("int64")).sum()),
        "arr_out_of_measured_range": int(((arr < arr_lo) | (arr > arr_hi)).sum()),
        "arr_out_of_window": int(((arr < lo) | (arr >= hi)).sum()),
        "dep_out_of_window": int(((dep[valid] < lo) | (dep[valid] >= hi)).sum()),
        "arr_s_min": int(arr.min()), "arr_s_max": int(arr.max()),
    }
    ok = (obs["dep_lt_arr_rows"] == 0 and obs["arr_out_of_measured_range"] == 0
          and obs["arr_out_of_window"] == 0 and obs["dep_out_of_window"] == 0)
    return core.check_true(
        "C-S00-A-005", "CONTRACT", ok, obs,
        {"dep_lt_arr_rows": 0, "arr_out_of_measured_range": 0,
         "arr_out_of_window": 0, "dep_out_of_window": 0,
         "measured_range": [ARR_FIRST_TS, ARR_LAST_TS]},
        "audit/schedule_after.md §3")


# ── C-S00-A-006: (pattern_id, master_seq)→stop_id 1:1 — 18,106 조합 ──────────
def c006_master_grid(ev: pd.DataFrame, rm: pd.DataFrame) -> core.CheckResult:
    grid = ev.groupby(["pattern_id", "master_seq"])["stop_id"].nunique()
    obs = {"combos": int(len(grid)), "conflicts": int((grid > 1).sum()),
           "route_master_rows": int(len(rm)),
           "route_master_key_dup": int(rm.duplicated(["pattern_id", "master_seq"]).sum())}
    exp = {"combos": raw_schedule_after.MASTER_GRID_COMBOS, "conflicts": 0,
           "route_master_rows": raw_schedule_after.MASTER_GRID_COMBOS,
           "route_master_key_dup": 0}
    return core.check_true(
        "C-S00-A-006", "CONTRACT", obs == exp, obs, exp, "audit/schedule_after.md §5.4",
        note="격자의 결번은 결측이 아니라 통과 stop 인덱스(급행 5001: 165칸 중 21정차) — "
             "'연속 정차 카운터' 가정·dense-rank 재부여 금지")


# ── C-S00-A-007: (obe, pattern, master_seq, arr) 원시 자연키 유일 ────────────
def c007_natural_key(ev: pd.DataFrame) -> core.CheckResult:
    return core.unique_key("C-S00-A-007", "CONTRACT", ev,
                           ["obe_id", "pattern_id", "master_seq", "arr_ts"],
                           "audit/schedule_after.md §6.4")


# ── C-S00-A-008: stop 우주 3,224 — events·route_agg와 양방향 집합 일치 ───────
def c008_stop_universe(ev: pd.DataFrame, ra: pd.DataFrame,
                       stops: pd.DataFrame) -> core.CheckResult:
    n_stops = paths.load_params()["raw_contracts"]["stops_after"]["rows"]
    stop_set = set(stops["stop_id"])
    ev_set = set(ev["stop_id"])
    ra_set = set(ra["stop_id"])
    obs = {"stops_rows": int(len(stops)),
           "stop_id_dup": int(stops["stop_id"].duplicated().sum()),
           "events_minus_stops": len(ev_set - stop_set),
           "stops_minus_events": len(stop_set - ev_set),
           "route_agg_minus_stops": len(ra_set - stop_set),
           "stops_minus_route_agg": len(stop_set - ra_set)}
    exp = {"stops_rows": n_stops, "stop_id_dup": 0,
           "events_minus_stops": 0, "stops_minus_events": 0,
           "route_agg_minus_stops": 0, "stops_minus_route_agg": 0}
    return core.check_true(
        "C-S00-A-008", "CONTRACT", obs == exp, obs, exp,
        "audit/schedule_after.md §6.1, audit/routes_stops.md §5",
        note="raw '^\\d+\\.0$' 전행 검증과 '.0' strip은 io/raw_stops.load_after 소속 — "
             "로딩 성공이 곧 raw 검증 기준 충족")


# ── C-S00-A-009: route_name 결측 716행 == 이름미상 3 pattern ─────────────────
def c009_nameless_routes(ev: pd.DataFrame) -> core.CheckResult:
    nameless = ev.loc[ev["route_name"].isna(), "pattern_id"]
    extra = sorted(set(nameless) - raw_schedule_after.NAMELESS_PATTERNS)
    obs = {"rows": int(len(nameless)), "patterns_outside_allowed": extra}
    exp = {"rows": raw_schedule_after.NAMELESS_ROWS, "patterns_outside_allowed": []}
    return core.check_true(
        "C-S00-A-009", "CONTRACT", obs == exp, obs, exp,
        "audit/schedule_after.md §1, §6.2",
        note=f"허용 pattern: {sorted(raw_schedule_after.NAMELESS_PATTERNS)} — "
             "이름은 유추 가능하나 미확정: 임의 명명 금지(s01에서 route=NULL 유지)")


# ── C-S00-A-010: route_agg 구조 + 집계 대조(비독립 경고) ─────────────────────
def c010_route_agg(ra: pd.DataFrame, ev: pd.DataFrame) -> core.CheckResult:
    ev_cells = ev.groupby(["pattern_id", "stop_id"]).size()
    ra_cells = ra.groupby(["pattern_id", "stop_id"])["도착횟수"].sum()
    j = pd.concat([ra_cells.rename("agg"), ev_cells.rename("ev")], axis=1)
    both = j.dropna()
    ev_only = j[j["agg"].isna()]
    ev_only_patterns = sorted({p for p, _ in ev_only.index})
    obs = {
        "rows": int(len(ra)),
        "key_dup": int(ra.duplicated(["pattern_id", "seq"]).sum()),
        "n_patterns": int(ra["pattern_id"].nunique()),
        "schedule_minus_agg_patterns": sorted(set(ev["pattern_id"]) - set(ra["pattern_id"])),
        "derived_flag_all_true": bool(ra["derived_from_schedule"].all()),
        "arr_list_len_mismatch": int((ra["arr_list_s"].map(len) != ra["도착횟수"]).sum()),
        "agg_only_cells": int(len(j) - len(both) - len(ev_only)),
        "matched_cells": int((both["agg"] == both["ev"]).sum()),
        "compared_cells": int(len(both)),
        "ev_only_patterns_outside_nameless":
            sorted(set(ev_only_patterns) - raw_schedule_after.NAMELESS_PATTERNS),
    }
    exp = {
        "rows": raw_bus_route_after.ROWS,
        "key_dup": 0,
        "n_patterns": raw_bus_route_after.N_PATTERNS,
        "schedule_minus_agg_patterns": sorted(raw_schedule_after.NAMELESS_PATTERNS),
        "derived_flag_all_true": True,
        "arr_list_len_mismatch": 0,
        "agg_only_cells": 0,
        "matched_cells": AGG_MATCH_CELLS,
        "compared_cells": raw_bus_route_after.ROWS,
        "ev_only_patterns_outside_nameless": [],
    }
    return core.check_true(
        "C-S00-A-010", "CONTRACT", obs == exp, obs, exp,
        "audit/routes_stops.md §1, audit/schedule_after.md §6.2, §6.3",
        note="비독립 경고: bus_route_after는 같은 날 로그의 집계본(17,840/17,841 일치) — "
             "이 체크는 '적재 무결성 확인'이지 '독립 검증'이 아니다(순환 논증 주의). "
             "불일치 1셀은 파일 자체 결함의 박제(감사 실측).")


# ── C-S00-A-011: 시각 포맷 겸용 — 도착횟수==1 전부 한국어, 나머지 전부 HH:MM:SS ──
def c011_dual_time_format(br_raw: pd.DataFrame) -> core.CheckResult:
    single = br_raw["도착횟수"] == "1"
    kr_rows = br_raw.loc[single, "도착시간들"]
    hms_rows = br_raw.loc[~single, "도착시간들"]
    kr_bad = int((~kr_rows.str.fullmatch(raw_bus_route_after.KR_TOKEN_RE.pattern)).sum())
    tokens = hms_rows.str.split("|", regex=False).explode()
    hms_bad = int((~tokens.str.fullmatch(raw_bus_route_after.HMS_TOKEN_RE.pattern)).sum())
    obs = {"kr_rows": int(len(kr_rows)), "kr_format_bad": kr_bad,
           "hms_rows": int(len(hms_rows)), "hms_token_bad": hms_bad}
    exp = {"kr_rows": raw_bus_route_after.KR_FORMAT_ROWS, "kr_format_bad": 0,
           "hms_rows": raw_bus_route_after.HMS_FORMAT_ROWS, "hms_token_bad": 0}
    return core.check_true(
        "C-S00-A-011", "CONTRACT", obs == exp, obs, exp, "audit/routes_stops.md §7",
        note="생성 파이프라인의 dtype 분기(단일값 vs 리스트) 흔적 — 혼합 행 0이 검증 규칙")


# ── P-S00-A-001†: 좌표 bbox 밖 0건 (감사 실측이라 BLOCK 유지) ────────────────
def p001_coord_bbox(ev: pd.DataFrame, stops: pd.DataFrame) -> core.CheckResult:
    c = paths.load_params()["raw_contracts"]["stops_after"]

    def out_cnt(lat: pd.Series, lon: pd.Series) -> int:
        return int((lat.isna() | lon.isna()
                    | (lat < c["lat_min"]) | (lat > c["lat_max"])
                    | (lon < c["lon_min"]) | (lon > c["lon_max"])).sum())

    obs = {"events_out_of_bbox": out_cnt(ev["lat"], ev["lon"]),
           "stops_out_of_bbox": out_cnt(stops["lat"], stops["lon"]),
           "zone_yangsan_rows_info": int((ev["zone"] == ZONE_INFO).sum())}
    ok = obs["events_out_of_bbox"] == 0 and obs["stops_out_of_bbox"] == 0
    return core.check_true(
        "P-S00-A-001", "CONTRACT", ok, obs,
        {"events_out_of_bbox": 0, "stops_out_of_bbox": 0},
        "audit/routes_stops.md §6",
        failure_means=["loader_bug", "logic_bug"],
        note="물리 개연성 체크지만 감사 실측(bbox 밖 0건)이라 BLOCK 게이트 유지 "
             "(verification.md §5.2 †, ID는 영구 불변이라 P- 접두 유지). "
             "zone '양산시' 행수는 정보 기록(양산 광역 경계 클리핑 [SA§1·§5.5]) — 게이트 아님.")


# ── 실패 표본 덤프 (운영 규율 6: 덤프 없는 FAIL은 하네스 결함) ────────────────
def _violation_frame(cid: str, ev, sched_raw, br_raw, ra, stops):
    if cid == "C-S00-A-003":
        bad = ~sched_raw["arrival_time"].str.fullmatch(timeparse.KR_TS_RE.pattern)
        return sched_raw[bad]
    if cid == "C-S00-A-004":
        dep_re = sched_raw["departure_time"].str.fullmatch(timeparse.KR_TS_RE.pattern)
        return sched_raw[~dep_re]
    if cid == "C-S00-A-005":
        lo, hi = _window()
        valid = ev["dep_s"].notna()
        bad = ((ev["arr_s"] < lo) | (ev["arr_s"] >= hi)
               | (valid & (ev["dep_s"] < ev["arr_s"]))
               | (valid & ((ev["dep_s"] < lo) | (ev["dep_s"] >= hi))))
        return ev[bad.fillna(False)]
    if cid == "C-S00-A-006":
        grid = ev.groupby(["pattern_id", "master_seq"])["stop_id"].nunique()
        conflicts = grid[grid > 1].index
        return ev.set_index(["pattern_id", "master_seq"]).loc[conflicts].reset_index() \
            if len(conflicts) else ev.iloc[0:0]
    if cid == "C-S00-A-007":
        key = ["obe_id", "pattern_id", "master_seq", "arr_ts"]
        return ev[ev.duplicated(key, keep=False)]
    if cid == "C-S00-A-008":
        diff_ids = ((set(ev["stop_id"]) | set(ra["stop_id"])) ^ set(stops["stop_id"]))
        return pd.DataFrame({"stop_id": sorted(diff_ids)})
    if cid == "C-S00-A-009":
        return ev[ev["route_name"].isna()
                  & ~ev["pattern_id"].isin(raw_schedule_after.NAMELESS_PATTERNS)]
    if cid == "C-S00-A-010":
        ev_cells = ev.groupby(["pattern_id", "stop_id"]).size().rename("ev")
        ra_cells = (ra.groupby(["pattern_id", "stop_id"])["도착횟수"].sum().rename("agg"))
        j = pd.concat([ra_cells, ev_cells], axis=1)
        return j[j["agg"] != j["ev"]].reset_index()
    if cid == "C-S00-A-011":
        single = br_raw["도착횟수"] == "1"
        kr_bad = single & ~br_raw["도착시간들"].str.fullmatch(
            raw_bus_route_after.KR_TOKEN_RE.pattern)
        hms_bad = ~single & ~br_raw["도착시간들"].str.split("|", regex=False).map(
            lambda ts: all(raw_bus_route_after.HMS_TOKEN_RE.match(t) for t in ts))
        return br_raw[kr_bad | hms_bad]
    if cid == "P-S00-A-001":
        c = paths.load_params()["raw_contracts"]["stops_after"]
        bad = (ev["lat"].isna() | ev["lon"].isna()
               | (ev["lat"] < c["lat_min"]) | (ev["lat"] > c["lat_max"])
               | (ev["lon"] < c["lon_min"]) | (ev["lon"] > c["lon_max"]))
        return ev[bad]
    return None


def run(ctx) -> list[core.CheckResult]:
    """체크 실행 진입점 — 러너 규약 run(ctx) -> list[CheckResult]."""
    ev = ctx.df("events.parquet")
    rm = ctx.df("route_master.parquet")
    stops = ctx.df("stops.parquet")
    ra = ctx.df("route_agg.parquet")
    sched_raw = raw_schedule_after.load()      # 로딩 = raw 불변식 재검증 (io 관문)
    br_raw = raw_bus_route_after.load()

    results = [
        c000_raw_hashes(),
        c001_raw_shape(sched_raw),
        c002_service_date(sched_raw["운행 일자"]),
        c003_arrival_format(sched_raw["arrival_time"]),
        c004_dep_sentinel(sched_raw["departure_time"], int(ev["dep_is_sentinel"].sum())),
        c005_time_bounds(ev),
        c006_master_grid(ev, rm),
        c007_natural_key(ev),
        c008_stop_universe(ev, ra, stops),
        c009_nameless_routes(ev),
        c010_route_agg(ra, ev),
        c011_dual_time_format(br_raw),
        p001_coord_bbox(ev, stops),
    ]
    for r in results:
        if r.failed and r.sample_path is None:
            bad = _violation_frame(r.check_id, ev, sched_raw, br_raw, ra, stops)
            if bad is not None and len(bad):
                r.sample_path = core.dump_sample(ctx.vdir, r.check_id, bad)
    return results
