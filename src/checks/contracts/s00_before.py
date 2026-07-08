"""s00_ingest / before 필수 검증 — C-S00-B-000~017, P-S00-B-001, D-S00-B-001 (verification.md §5.1).

expected는 전부 감사 실측(reference/audit/*.md) 또는 prior_baseline이 출처다.
감사 실측 이상치는 positive_control=True — 이상치가 0건이 되어도 FAIL(검증기의 눈).
개별 체크 함수는 데이터프레임/메타를 직접 받는다 — 위반 주입 테스트가 단독 호출한다.

출처 약어: [SB]=audit/schedule_before.md, [RS]=audit/routes_stops.md, [VT]=audit/variant_tags.md.
아래 상수는 감사 실측 기준값이다(임계값 아님 — 튜닝 대상 아님).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import manifest
import paths
from checks import core, diff
from dataio import normalize, raw_variant_tags, timeparse

# ── 감사 실측 감사 기준 상수 ──────────────────────────────────────────────────────
_RAW_FILES = [                                       # C-S00-B-000 대상 (로더 4종의 원천)
    "data/ulsan_route_schedule_before.parquet",
    "data/ulsan_bus_route_before.parquet",
    "data/ulsan_stops_before.parquet",
    "reference/variant_tagging/variant_tags.csv",
]
_SCHEDULE_RAW = {"raw_rows": 427_527, "allnull_rows": 48, "valid_rows": 427_479}  # [SB§1,§7.1]
_COUNTS = {"trips": 7_625, "patterns": 487, "route_names": 398,
           "bases": 190, "stops": 3_397}                       # [SB§2]
# [SB§10] 감사 표기 'BR_TAGO_USB+12자리' = 'BR_TAGO_' 뒤 12자(USB+숫자9) — 실측 총 20자
# (variant_tags.md §1 '20자' 정합. \d{12} 원문 표기는 자릿수 셈 방식 차이).
_ID_RE = {"pattern_id": r"BR_(TAGO_USB\d{9}|ACC0_\d{8})",
          "stop_id": r"BS_(TAGO_USB\d{9}|ACC0_\d{8})"}
_DWELL_LIMIT_S = 600                                            # [SB§4] dwell>10분 이상치 정의
_DWELL_ROWS = 31                                                # [SB§4] 전부 ACC0
_SEQ_VIOL = {"trips": 18, "offset": 8, "gap": 10}               # [SB§5] 전부 ACC0
_ACC0_MULTI = {"울주01": 3, "울주02": 2, "울주04": 3,
               "울주05": 5, "울주08": 2, "울주09": 2}            # [SB§3,§6] 복수 정차열 6패턴
_STOPS_TOTAL = 3_409                                            # [RS§5]
_UNUSED = {"total": 12, "by_lineage": {"KTDB": 8, "TAGO": 4}}   # [SB§8][RS§5]
_UNUSED_TAGO_NAMES = ["내고산", "외고산마을입구", "중고산", "중고산마을입구"]  # [SB§7.1] 정렬
_RU = {"rows": 21_402, "route_names": 400}                      # [RS§1,§3]
_RU_ONLY_NAMES = {"50(내고산 방면)", "김해공항"}                  # [RS§3][SB§8]
_CELLS = {"total": 17_675, "equal": 17_661, "mismatch": 14}     # [RS§3] 노선×정류장명 셀 대조
_CELL_MISMATCH_BASE_PREFIX = "33"   # [RS§3] '33(현대백화점 순환) 계열' — 실측 분해 33:11셀 + 337:3셀
_VT_ROWS = 481                                                  # [VT§1]
_ROLE_DIST = {"main": 187, "circular": 103, "short_turn": 112, "branch": 14,
              "detour": 51, "extension": 10, "duplicate": 2, "anomaly": 2}  # [VT§2]
_BASE_REF = {"main_self": 92, "circular_base": 37,
             "circular_self": 34, "circular_other": 3}          # [VT§3]
_EVIDENCE = {"files": 184, "variants": 481}                     # [VT§5]
_ROUTES_TAGGED = 184                                            # [VT§1] tags route distinct
_ULSAN_BBOX = {"lat_min": 35.2, "lat_max": 35.85,
               "lon_min": 128.9, "lon_max": 129.6}              # [RS§6] 울산 bbox — 밖 정확히 2건
_BBOX_OUT_NAMES = {"국내선", "국제선청사"}                        # [RS§6] 김해공항 국제선청사·국내선
_KOREA_BBOX = {"lat_min": 33.0, "lat_max": 39.0,
               "lon_min": 124.0, "lon_max": 132.0}              # [RS§6] '한국 밖 0'·위경도 스왑 검출용
_SUPPORT_TOKEN = "지원"                                          # [SB§3.1] 지원 6 판별(이름 규칙)
_LIMOUSINE_NAME = "김해공항"                                     # [RS§4] 리무진 1
_EXPRESS_RE = r"\d{4}"                                          # [RS§4] 급행 = 4자리 숫자
_GENERAL_NUM_RE = r"\d{1,3}"                                    # [RS§4] 일반 숫자 160
_ULJU_RE = r"울주\d{2}"                                          # [RS§4] 울주 10


def _dump(r: core.CheckResult, vdir, df) -> None:
    """실패 표본 의무(verification.md §7 규율 6) — FAIL이면 _debug/ 덤프."""
    if vdir is not None and r.failed and df is not None and len(df):
        r.sample_path = core.dump_sample(vdir, r.check_id, df)


def _service_window() -> tuple[int, int]:
    t = paths.load_params()["time"]
    return (t["service_min_h"] * timeparse.SEC_PER_HOUR,
            t["service_max_h"] * timeparse.SEC_PER_HOUR)


# ── CONTRACT ─────────────────────────────────────────────────────────────────
def c000_raw_hashes() -> core.CheckResult:
    """C-S00-B-000: raw sha256 == 기준값 — data_drift와 loader_bug를 구분하는 0번 체크."""
    import yaml
    with open(paths.BASELINE_DIR / "raw_hashes.yaml", encoding="utf-8") as f:
        frozen = yaml.safe_load(f)["files"]
    mismatch = [rel for rel in _RAW_FILES
                if manifest.sha256_file(paths.ROOT / rel) != frozen.get(rel)]
    return core.check_true("C-S00-B-000", "CONTRACT", not mismatch,
                           {"mismatch_files": mismatch}, {"mismatch_files": []},
                           "reference/prior_baseline/raw_hashes.yaml",
                           failure_means=["data_drift"])


def c001_encoding(meta: dict) -> core.CheckResult:
    """C-S00-B-001: utf-8-sig 로딩·BOM 없음·컬럼 순서 정확 일치 (로더 관찰값)."""
    obs = {"schedule_bom_ok": meta.get("schedule", {}).get("bom_ok"),
           "schedule_columns_ok": meta.get("schedule", {}).get("columns_ok"),
           "bus_route_bom_ok": meta.get("bus_route", {}).get("bom_ok"),
           "bus_route_columns_ok": meta.get("bus_route", {}).get("columns_ok")}
    return core.check_true("C-S00-B-001", "CONTRACT", all(v is True for v in obs.values()),
                           obs, {k: True for k in obs}, "audit/schedule_before.md §1; routes_stops.md §1",
                           note="stops·variant_tags의 BOM/컬럼은 각 로더가 로딩 시 assert")


def c002_schedule_rows(meta: dict, st: pd.DataFrame) -> core.CheckResult:
    """C-S00-B-002: 원시 427,527·전결측 정확 48(연속 블록)·부분결측 0·유효 427,479·중복 0."""
    sm = meta.get("schedule", {})
    obs = {"raw_rows": sm.get("raw_rows"), "allnull_rows": sm.get("allnull_rows"),
           "allnull_contiguous": sm.get("allnull_contiguous"),
           "partial_null_rows": sm.get("partial_null_rows"),
           "dup_full_rows": sm.get("dup_full_rows"),
           "artifact_rows": int(len(st)), "artifact_nulls": int(st.isna().sum().sum())}
    exp = {"raw_rows": _SCHEDULE_RAW["raw_rows"], "allnull_rows": _SCHEDULE_RAW["allnull_rows"],
           "allnull_contiguous": True, "partial_null_rows": 0, "dup_full_rows": 0,
           "artifact_rows": _SCHEDULE_RAW["valid_rows"], "artifact_nulls": 0}
    return core.check_true("C-S00-B-002", "CONTRACT", obs == exp, obs, exp,
                           "audit/schedule_before.md §1, §7.1")


def c003_row_key(st: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-003: (trip_id, seq) 유일 — 행 유일 키."""
    r = core.unique_key("C-S00-B-003", "CONTRACT", st, ["trip_id", "seq"],
                        "audit/schedule_before.md §2")
    _dump(r, vdir, st[st.duplicated(["trip_id", "seq"], keep=False)])
    return r


def c004_cardinalities(st: pd.DataFrame) -> core.CheckResult:
    """C-S00-B-004: trip 7,625 / pattern 487 / route_name 398 / base 190 / stop 3,397."""
    names = pd.Series(st["route_name"].unique())
    obs = {"trips": int(st["trip_id"].nunique()),
           "patterns": int(st["pattern_id"].nunique()),
           "route_names": int(names.nunique()),
           "bases": int(names.map(normalize.base_route_name).nunique()),
           "stops": int(st["stop_id"].nunique())}
    return core.check_true("C-S00-B-004", "CONTRACT", obs == _COUNTS, obs, _COUNTS,
                           "audit/schedule_before.md §2")


def c005_trip_format(st: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-005: trip_id == pattern_id+_Ord+3자리 전건; pattern→route_name 함수적."""
    t = st[["trip_id", "pattern_id"]].drop_duplicates()
    fmt_ok = t["trip_id"].str.fullmatch(_ID_RE["pattern_id"] + r"_Ord\d{3}")
    prefix_ok = t["trip_id"].str.replace(r"_Ord\d{3}$", "", regex=True) == t["pattern_id"]
    bad_fmt = int((~(fmt_ok & prefix_ok)).sum())
    func_bad = int((st.groupby("pattern_id")["route_name"].nunique() > 1).sum())
    obs = {"trip_id_format_bad": bad_fmt, "pattern_to_route_name_violations": func_bad}
    exp = {"trip_id_format_bad": 0, "pattern_to_route_name_violations": 0}
    r = core.check_true("C-S00-B-005", "CONTRACT", obs == exp, obs, exp,
                        "audit/schedule_before.md §2")
    _dump(r, vdir, t[~(fmt_ok & prefix_ok)])
    return r


def c006_id_regex(st: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-006: ID regex 2계보(TAGO/ACC0) 전건."""
    bad_pat = ~st["pattern_id"].str.fullmatch(_ID_RE["pattern_id"])
    bad_stop = ~st["stop_id"].str.fullmatch(_ID_RE["stop_id"])
    obs = {"pattern_id_bad": int(bad_pat.sum()), "stop_id_bad": int(bad_stop.sum())}
    exp = {"pattern_id_bad": 0, "stop_id_bad": 0}
    r = core.check_true("C-S00-B-006", "CONTRACT", obs == exp, obs, exp,
                        "audit/schedule_before.md §10")
    _dump(r, vdir, st[bad_pat | bad_stop])
    return r


def c007_times(meta: dict, st: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-007: 시각 regex 전건·service_s 창·행내 dep≥arr·trip 단조·첫 정류장 arr==dep."""
    lo, hi = _service_window()
    s = st.sort_values(["trip_id", "seq"], kind="mergesort")
    next_arr = s.groupby("trip_id")["arr_s"].shift(-1)
    mono_bad = int((next_arr < s["dep_s"]).sum())
    first = s.groupby("trip_id").first()
    obs = {"time_format_mismatch": meta.get("schedule", {}).get("time_format_mismatch"),
           "out_of_window": int(((st["arr_s"] < lo) | (st["arr_s"] >= hi)
                                 | (st["dep_s"] < lo) | (st["dep_s"] >= hi)).sum()),
           "dep_lt_arr": int((st["dep_s"] < st["arr_s"]).sum()),
           "monotonic_violations": mono_bad,
           "first_stop_arr_ne_dep": int((first["arr_s"] != first["dep_s"]).sum()),
           "trips": int(len(first))}
    exp = {"time_format_mismatch": 0, "out_of_window": 0, "dep_lt_arr": 0,
           "monotonic_violations": 0, "first_stop_arr_ne_dep": 0,
           "trips": _COUNTS["trips"]}
    r = core.check_true("C-S00-B-007", "CONTRACT", obs == exp, obs, exp,
                        "audit/schedule_before.md §4")
    _dump(r, vdir, st[(st["dep_s"] < st["arr_s"])
                      | (st["arr_s"] < lo) | (st["arr_s"] >= hi)
                      | (st["dep_s"] < lo) | (st["dep_s"] >= hi)])
    return r


def c008_dwell_pc(st: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-008 [PC]: dwell>600s 정확히 31행, 전부 ACC0 — 양성 대조군."""
    viol = st[(st["dep_s"] - st["arr_s"]) > _DWELL_LIMIT_S]
    lineages = sorted(viol["lineage"].unique().tolist())
    obs = {"rows": int(len(viol)), "lineages": lineages}
    exp = {"rows": _DWELL_ROWS, "lineages": ["ACC0"]}
    r = core.check_true("C-S00-B-008", "CONTRACT", obs == exp, obs, exp,
                        "audit/schedule_before.md §4", positive_control=True,
                        note="이상치 31행이 사라져도(0건) FAIL — 검증기의 눈")
    _dump(r, vdir, viol)
    return r


def c009_seq_pc(st: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-009 [PC]: seq 1..n 연속 위반 정확히 18 trips, 전부 ACC0 (offset 8 + 결번 10)."""
    g = st.groupby("trip_id")["seq"]
    stats = pd.DataFrame({"mn": g.min(), "mx": g.max(), "cnt": g.size(), "nun": g.nunique()})
    stats["lineage"] = st.groupby("trip_id")["lineage"].first()
    viol = stats[(stats["mn"] != 1) | (stats["mx"] != stats["cnt"])
                 | (stats["nun"] != stats["cnt"])]
    offset = viol[(viol["mn"] != 1) & ((viol["mx"] - viol["mn"] + 1) == viol["cnt"])
                  & (viol["nun"] == viol["cnt"])]
    obs = {"trips": int(len(viol)), "offset": int(len(offset)),
           "gap": int(len(viol) - len(offset)),
           "non_acc0": int((viol["lineage"] != "ACC0").sum())}
    exp = {"trips": _SEQ_VIOL["trips"], "offset": _SEQ_VIOL["offset"],
           "gap": _SEQ_VIOL["gap"], "non_acc0": 0}
    r = core.check_true("C-S00-B-009", "CONTRACT", obs == exp, obs, exp,
                        "audit/schedule_before.md §5", positive_control=True)
    _dump(r, vdir, viol.reset_index())
    return r


def c010_pattern_determinism(st: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-010: TAGO pattern 고유 정차열 정확히 1개 — 복수는 ACC0 6패턴뿐."""
    s = st.sort_values(["trip_id", "seq"], kind="mergesort")
    pat = s.groupby("trip_id")["stop_id"].agg(tuple)
    head = s.drop_duplicates("trip_id").set_index("trip_id")
    per = pd.DataFrame({"p": pat, "pattern_id": head["pattern_id"],
                        "route_name": head["route_name"]})
    npat = per.groupby("pattern_id")["p"].nunique()
    multi = npat[npat > 1]
    tago_multi = int(multi.index.str.startswith("BR_TAGO").sum())
    name_of = per.drop_duplicates("pattern_id").set_index("pattern_id")["route_name"]
    multi_by_base = {normalize.base_route_name(name_of[pid]): int(n)
                     for pid, n in multi.items()}
    obs = {"tago_multi": tago_multi, "multi_count": int(len(multi)),
           "multi_by_base": dict(sorted(multi_by_base.items()))}
    exp = {"tago_multi": 0, "multi_count": len(_ACC0_MULTI),
           "multi_by_base": dict(sorted(_ACC0_MULTI.items()))}
    r = core.check_true("C-S00-B-010", "CONTRACT", obs == exp, obs, exp,
                        "audit/schedule_before.md §3, §6")
    _dump(r, vdir, per[per["pattern_id"].isin(multi.index)].reset_index())
    return r


def c011_stop_fk(st: pd.DataFrame, stops: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-011: schedule stop 전량 ⊂ stops; 미사용 12 == KTDB 8 + 내고산 계열 4."""
    dangling = sorted(set(st["stop_id"].unique()) - set(stops["stop_id"]))
    unused = stops[~stops["in_schedule"]]
    obs = {"dangling": len(dangling),
           "unused_total": int(len(unused)),
           "unused_by_lineage": {k: int(v) for k, v in
                                 sorted(unused["lineage"].value_counts().items())},
           "unused_tago_names": sorted(unused.loc[unused["lineage"] == "TAGO",
                                                  "stop_name"].tolist())}
    exp = {"dangling": 0, "unused_total": _UNUSED["total"],
           "unused_by_lineage": dict(sorted(_UNUSED["by_lineage"].items())),
           "unused_tago_names": _UNUSED_TAGO_NAMES}
    r = core.check_true("C-S00-B-011", "CONTRACT", obs == exp, obs, exp,
                        "audit/schedule_before.md §8; routes_stops.md §5")
    _dump(r, vdir, unused)
    return r


def c012_stop_identity(stops: pd.DataFrame, meta: dict) -> core.CheckResult:
    """C-S00-B-012: stop_id 유일 3,409; schedule stop_id→이름 함수적; 유니크(명,좌표)==3,409."""
    obs = {"stop_rows": int(len(stops)),
           "stop_id_dup": int(stops["stop_id"].duplicated().sum()),
           "schedule_stop_name_functional_violations":
               meta.get("schedule", {}).get("stop_name_functional_violations"),
           "bus_route_unique_name_coord":
               meta.get("bus_route", {}).get("unique_name_coord")}
    exp = {"stop_rows": _STOPS_TOTAL, "stop_id_dup": 0,
           "schedule_stop_name_functional_violations": 0,
           "bus_route_unique_name_coord": _STOPS_TOTAL}
    return core.check_true("C-S00-B-012", "CONTRACT", obs == exp, obs, exp,
                           "routes_stops.md §1, §5")


def c013_joiner_pc(meta: dict, max_dist_m: float) -> core.CheckResult:
    """C-S00-B-013 [PC]: name→stop 해소 — alias 정확 1건·최근접 ≤1m·실패 0·3,409 stop 매핑."""
    j = meta.get("join", {})
    obs = {"n_alias": j.get("n_alias"), "n_fail": j.get("n_fail"),
           "resolved_max_dist_m": j.get("resolved_max_dist_m"),
           "resolved_stop_nunique": j.get("resolved_stop_nunique")}
    ok = (obs["n_alias"] == 1 and obs["n_fail"] == 0
          and obs["resolved_max_dist_m"] is not None
          and obs["resolved_max_dist_m"] <= max_dist_m
          and obs["resolved_stop_nunique"] == _STOPS_TOTAL)
    exp = {"n_alias": 1, "n_fail": 0, "resolved_max_dist_m": f"<= {max_dist_m}",
           "resolved_stop_nunique": _STOPS_TOTAL}
    return core.check_true("C-S00-B-013", "CONTRACT", ok, obs, exp,
                           "routes_stops.md §5", positive_control=True,
                           note="alias = 양우내안에→양우내안애 1건 (실측 최근접 max 0.91m)")


def c014_route_union(ru: pd.DataFrame, st: pd.DataFrame, vdir=None) -> core.CheckResult:
    """C-S00-B-014: (route_name,seq) 유일 21,402; seq 1..N 연속; 노선명 400; 차집합 정확 2."""
    g = ru.groupby("route_name")["seq"]
    ru_names = set(ru["route_name"].unique())
    st_names = set(st["route_name"].unique())
    obs = {"rows": int(len(ru)),
           "dup_key": int(ru.duplicated(["route_name", "seq"]).sum()),
           "seq_noncontiguous_routes": int((~((g.min() == 1) & (g.max() == g.size()))).sum()),
           "route_names": int(len(ru_names)),
           "union_only": sorted(ru_names - st_names),
           "schedule_only": sorted(st_names - ru_names)}
    exp = {"rows": _RU["rows"], "dup_key": 0, "seq_noncontiguous_routes": 0,
           "route_names": _RU["route_names"],
           "union_only": sorted(_RU_ONLY_NAMES), "schedule_only": []}
    r = core.check_true("C-S00-B-014", "CONTRACT", obs == exp, obs, exp,
                        "routes_stops.md §1, §3; schedule_before.md §8")
    _dump(r, vdir, ru[ru.duplicated(["route_name", "seq"], keep=False)])
    return r


def c015_arrival_accounting(ru: pd.DataFrame, st: pd.DataFrame, stops: pd.DataFrame,
                            vdir=None) -> core.CheckResult:
    """C-S00-B-015: 도착횟수==토큰수 전행; 셀 대조 17,661/17,675 — 불일치 정확 14셀(33 계열).

    셀 = (노선명, 정류장명) — stop 이름은 stops 표준 표기(name→stop 해소 결과)로 통일.
    불일치 14셀은 파일 자체 결함의 박제(33 계열 13 trips 과소) — 실측 분해: 33 11셀 + 337 3셀.
    """
    token_bad = int(((ru["arr_list_s"].map(len) != ru["도착횟수"])
                     | (ru["dep_list_s"].map(len) != ru["도착횟수"])).sum())
    name_map = stops.set_index("stop_id")["stop_name"]
    common = set(st["route_name"].unique())
    rc = ru[ru["route_name"].isin(common)]
    cell_rb = (rc.assign(nm=rc["stop_id"].map(name_map))
               .groupby(["route_name", "nm"])["도착횟수"].sum())
    cell_sch = (st.groupby(["route_name", "stop_id"]).size().reset_index(name="n")
                .assign(nm=lambda d: d["stop_id"].map(name_map))
                .groupby(["route_name", "nm"])["n"].sum())
    joined = pd.DataFrame({"rb": cell_rb}).join(pd.DataFrame({"sch": cell_sch}), how="outer")
    one_sided = joined[joined.isna().any(axis=1)]
    both = joined.dropna()
    neq = both[both["rb"] != both["sch"]]
    bases = [normalize.base_route_name(rn) for rn in
             neq.index.get_level_values("route_name")]
    obs = {"token_count_mismatch": token_bad,
           "cells_total": int(len(both)), "cells_one_sided": int(len(one_sided)),
           "cells_equal": int(len(both) - len(neq)), "cells_mismatch": int(len(neq)),
           "mismatch_outside_33": int(sum(not b.startswith(_CELL_MISMATCH_BASE_PREFIX)
                                          for b in bases))}
    exp = {"token_count_mismatch": 0,
           "cells_total": _CELLS["total"], "cells_one_sided": 0,
           "cells_equal": _CELLS["equal"], "cells_mismatch": _CELLS["mismatch"],
           "mismatch_outside_33": 0}
    r = core.check_true("C-S00-B-015", "CONTRACT", obs == exp, obs, exp,
                        "routes_stops.md §3, §9",
                        note="불일치 14셀 = 33(현대백화점 순환) 계열 — 파일 자체 결함의 박제 "
                             "(실측 분해: 33 11셀 + 337 3셀, base 전부 '33' 시작)")
    _dump(r, vdir, neq.reset_index())
    return r


def c016_variant_tags(vt: pd.DataFrame, tr: pd.DataFrame, ev_nstops: dict,
                      vdir=None) -> core.CheckResult:
    """C-S00-B-016: (481)·PK·role 분포 exact·frequency==trip수·base 무결·동치·n_stops==evidence."""
    freq_map = tr.groupby("pattern_id").size()
    freq_bad = int((vt["frequency"] != vt["pattern_id"].map(freq_map)).sum())
    base = vt["base_pattern_id_raw"]
    dangling = sorted(set(base.dropna().unique()) - set(vt["pattern_id"]))
    mb = vt[(vt["role"] == "main") & base.notna()]
    cb = vt[(vt["role"] == "circular") & base.notna()]
    obs = {"rows": int(len(vt)),
           "pattern_id_dup": int(vt["pattern_id"].duplicated().sum()),
           "role_dist": {k: int(v) for k, v in sorted(vt["role"].value_counts().items())},
           "frequency_mismatch": freq_bad,
           "base_dangling": len(dangling),
           "main_with_base": int(len(mb)),
           "main_base_nonself": int((mb["base_pattern_id_raw"] != mb["pattern_id"]).sum()),
           "circular_with_base": int(len(cb)),
           "circular_self": int((cb["base_pattern_id_raw"] == cb["pattern_id"]).sum()),
           "agent_verified_equiv_violations":
               int(((vt["source"] == "agent") != vt["verified"]).sum()),
           "n_stops_evidence_mismatch":
               int((vt["n_stops"] != vt["pattern_id"].map(ev_nstops)).sum())}
    exp = {"rows": _VT_ROWS, "pattern_id_dup": 0,
           "role_dist": dict(sorted(_ROLE_DIST.items())),
           "frequency_mismatch": 0, "base_dangling": 0,
           "main_with_base": _BASE_REF["main_self"], "main_base_nonself": 0,
           "circular_with_base": _BASE_REF["circular_base"],
           "circular_self": _BASE_REF["circular_self"],
           "agent_verified_equiv_violations": 0, "n_stops_evidence_mismatch": 0}
    r = core.check_true("C-S00-B-016", "CONTRACT", obs == exp, obs, exp,
                        "variant_tags.md §1, §2, §3",
                        note="main 자기참조 92 == 'no base' 표기 변형 — 정규화는 s01 소속, "
                             "circular 자기참조 34 보존이 379의 성립 조건")
    _dump(r, vdir, vt[vt["frequency"] != vt["pattern_id"].map(freq_map)])
    return r


def c017_evidence(vt: pd.DataFrame, evidence: dict, stops: pd.DataFrame) -> core.CheckResult:
    """C-S00-B-017: evidence 184 == route 184 정확 1:1; Σn_variants==481; stop_ids⊂stops 100%."""
    routes = set(vt["route"].unique())
    ev_stop_ids = {sid for doc in evidence.values()
                   for v in doc["variants"] for sid in v["stop_ids"]}
    obs = {"files": len(evidence), "routes": int(len(routes)),
           "file_route_diff": len(set(evidence.keys()) ^ routes),
           "sum_n_variants": int(sum(doc["n_variants"] for doc in evidence.values())),
           "stop_ids_missing": len(ev_stop_ids - set(stops["stop_id"]))}
    exp = {"files": _EVIDENCE["files"], "routes": _ROUTES_TAGGED, "file_route_diff": 0,
           "sum_n_variants": _EVIDENCE["variants"], "stop_ids_missing": 0}
    return core.check_true("C-S00-B-017", "CONTRACT", obs == exp, obs, exp,
                           "variant_tags.md §5")


def p001_coords(stops: pd.DataFrame, vdir=None) -> core.CheckResult:
    """P-S00-B-001†: 좌표 NaN·0·스왑·한국 밖 0; 울산 bbox 밖 정확히 2건 == 김해공항 2 stop.

    †감사 실측 범위(BLOCK 유지 — verification.md §5.1). 러너 게이트가 check_class로만
    차단하므로 class=CONTRACT로 등재한다(ID는 체크 대장의 P- 그대로 영구 불변).
    """
    lat, lon = stops["lat"], stops["lon"]
    korea_out = ((lat < _KOREA_BBOX["lat_min"]) | (lat > _KOREA_BBOX["lat_max"])
                 | (lon < _KOREA_BBOX["lon_min"]) | (lon > _KOREA_BBOX["lon_max"]))
    bbox_out = ((lat < _ULSAN_BBOX["lat_min"]) | (lat > _ULSAN_BBOX["lat_max"])
                | (lon < _ULSAN_BBOX["lon_min"]) | (lon > _ULSAN_BBOX["lon_max"]))
    out_df = stops[bbox_out]
    obs = {"nan": int(lat.isna().sum() + lon.isna().sum()),
           "zero": int(((lat == 0) | (lon == 0)).sum()),
           "outside_korea": int(korea_out.sum()),
           "outside_ulsan_bbox": int(bbox_out.sum()),
           "outside_names": sorted(out_df["stop_name"].unique().tolist())}
    exp = {"nan": 0, "zero": 0, "outside_korea": 0, "outside_ulsan_bbox": 2,
           "outside_names": sorted(_BBOX_OUT_NAMES)}
    r = core.check_true("P-S00-B-001", "CONTRACT", obs == exp, obs, exp,
                        "routes_stops.md §6",
                        note="† 감사 실측 범위 — BLOCK 유지 (bbox 밖 2건은 오류가 아니라 "
                             "실재 시외 정류장: 김해공항 국제선청사·국내선)")
    _dump(r, vdir, out_df)
    return r


def d001_route_classes(st: pd.DataFrame, ru: pd.DataFrame) -> core.CheckResult:
    """D-S00-B-001: Express 14 / General 170(숫자160+울주10) / base−지원==184 (union 191−6−1==184)."""
    reference_values = diff.load_reference_values()
    exp_e = diff.reference_value(reference_values, "before.express_routes")
    exp_g = diff.reference_value(reference_values, "before.general_routes")
    exp_regular = exp_e + exp_g

    bases = pd.Series(sorted({normalize.base_route_name(n)
                              for n in st["route_name"].unique()}))
    ubases = pd.Series(sorted({normalize.base_route_name(n)
                               for n in ru["route_name"].unique()}))
    n_express = int(bases.str.fullmatch(_EXPRESS_RE).sum())
    n_general = int(bases.str.fullmatch(_GENERAL_NUM_RE).sum()
                    + bases.str.fullmatch(_ULJU_RE).sum())
    n_support = int(bases.str.contains(_SUPPORT_TOKEN, regex=False).sum())
    n_limo_u = int((ubases == _LIMOUSINE_NAME).sum())
    obs = {"express": n_express, "general": n_general,
           "schedule_base_minus_support": int(len(bases)) - n_support,
           "union_base_minus_support_minus_limousine":
               int(len(ubases))
               - int(ubases.str.contains(_SUPPORT_TOKEN, regex=False).sum()) - n_limo_u}
    exp = {"express": exp_e, "general": exp_g,
           "schedule_base_minus_support": exp_regular,
           "union_base_minus_support_minus_limousine": exp_regular}

    status, note = ("MATCH", "") if obs == exp else (None, None)
    if status is None:
        documented = next((item for item in diff.load_known_deviations()
                           if item.check == "D-S00-B-001"), None)
        if documented is not None and obs == documented.measured:
            status, note = "EXPLAINED", f"{documented.id} ({documented.status}) — {documented.doc}"
        else:
            status, note = "UNEXPLAINED", "대장 미등재 편차"
    r = core.CheckResult(
        check_id="D-S00-B-001", check_class="DIFF", severity="SIGNAL", status=status,
        observed=obs, expected=exp,
        source="prior_baseline; routes_stops.md §4; schedule_before.md §3",
        failure_means=["convention_mismatch", "baseline_stale", "logic_bug"],
        note=note or "General 170 = 숫자 1~3자리 160 + 울주 10 (울주 마을버스 포함이 전제)")
    if status == "UNEXPLAINED":
        note_path = diff.make_investigation_note(
            "s00-before-route-class-counts", "D-S00-B-001", obs, exp)
        r.action_hint = f"조사 메모: {note_path}"
    return r


# ── 실행기 진입점 ────────────────────────────────────────────────────────────
def run(ctx) -> list:
    st = ctx.df("stop_times.parquet")
    tr = ctx.df("trips.parquet")
    stops = ctx.df("stops.parquet")
    ru = ctx.df("route_union.parquet")
    vt = ctx.df("variant_tags.parquet")
    with open(Path(ctx.vdir) / "ingest_meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    evidence = raw_variant_tags.load_evidence()
    ev_nstops = {v["route_id"]: len(v["stop_ids"])
                 for doc in evidence.values() for v in doc["variants"]}
    max_dist_m = paths.load_params()["join"]["max_dist_m"]
    v = ctx.vdir
    return [
        c000_raw_hashes(),
        c001_encoding(meta),
        c002_schedule_rows(meta, st),
        c003_row_key(st, v),
        c004_cardinalities(st),
        c005_trip_format(st, v),
        c006_id_regex(st, v),
        c007_times(meta, st, v),
        c008_dwell_pc(st, v),
        c009_seq_pc(st, v),
        c010_pattern_determinism(st, v),
        c011_stop_fk(st, stops, v),
        c012_stop_identity(stops, meta),
        c013_joiner_pc(meta, max_dist_m),
        c014_route_union(ru, st, v),
        c015_arrival_accounting(ru, st, stops, v),
        c016_variant_tags(vt, tr, ev_nstops, v),
        c017_evidence(vt, evidence, stops),
        p001_coords(stops, v),
        d001_route_classes(st, ru),
    ]
