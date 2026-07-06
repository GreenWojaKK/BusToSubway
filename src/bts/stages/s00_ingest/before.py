"""s00_ingest / before — 원시 데이터 로딩과 형태 정규화 (design.md §5 s00, stage1_canonical_spec.md §4.1).

산출물 5종(전부 컬럼 즉시 개명 완료):
  stop_times.parquet(427,479) / trips.parquet(7,625) / stops.parquet(3,409)
  / route_union.parquet(21,402) / variant_tags.parquet(481)
+ ingest_meta.json — 로더가 관찰한 원시 입력 정보(체크 모듈의 입력).

이 단계는 role 해석, base 참조 정규화, canonical 선정, 병합을 수행하지 않는다.
base_pattern_id_raw는 자기참조를 포함해 원래 값을 보존하고, 정규화는 s01에서 처리한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import bts.paths as paths
from bts.io import normalize, timeparse
from bts.io import raw_bus_route_before, raw_schedule_before, raw_variant_tags
from bts.io import raw_stops

# variant_tags.parquet 스키마 (design.md §5 s00 표 — 개명 완료 15컬럼)
_VT_COLUMNS = ["route", "route_type", "pattern_id", "dir_label", "n_stops", "frequency",
               "is_loop", "first", "last", "role", "direction_group",
               "base_pattern_id_raw", "confidence", "source", "verified"]


def _lineage(ids: pd.Series) -> pd.Series:
    """id 접두어에서 lineage {TAGO, ACC0, KTDB}를 추출한다."""
    return ids.str.extract(r"^B[RS]_([A-Z0-9]+)_", expand=False)


def _hms_map(series_list: list[pd.Series]) -> dict[str, int]:
    """유일 시각 문자열만 hms24_to_sec에 넘겨 같은 파싱 경로를 유지한다."""
    uniq = pd.unique(pd.concat(series_list, ignore_index=True))
    return {s: timeparse.hms24_to_sec(s) for s in uniq}


def _write_parquet(df: pd.DataFrame, path: Path) -> Path:
    paths.assert_writable(path)
    df.to_parquet(path, index=False)
    return path


def build(inputs, params, vdir: Path) -> dict[str, Path]:
    """로더 4종 → 컬럼·타입 표준화 → 파생(trips/stops) → name→stop 해소 → 산출."""
    vdir = Path(vdir)
    meta: dict = {"schedule": {}, "bus_route": {}, "variant_tags": {}, "join": {}}

    # 1) 로더 호출 (원시 입력 형태가 맞지 않으면 로더가 ContractViolation을 낸다)
    sch = raw_schedule_before.load(meta=meta["schedule"])
    raw_stop_rows = raw_stops.load_before()
    bus = raw_bus_route_before.load(meta=meta["bus_route"])
    tags = raw_variant_tags.load(meta=meta["variant_tags"])

    # 2) stop_times — route_id→pattern_id, stop_id 유지 + 시각→service_s
    tmap = _hms_map([sch["arrival_time"], sch["departure_time"]])
    stop_times = pd.DataFrame({
        "trip_id": sch["trip_id"],
        "pattern_id": sch["route_id"],
        "route_name": sch["route_name"],
        "stop_id": sch["stop_id"],
        "seq": sch["stop_sequence"].astype("int16"),
        "arr_s": sch["arrival_time"].map(tmap).astype("int32"),
        "dep_s": sch["departure_time"].map(tmap).astype("int32"),
        "lineage": _lineage(sch["route_id"]),
    }).sort_values(["trip_id", "seq"], kind="mergesort").reset_index(drop=True)

    # 3) trips — trip별 집계 (seq 순 정렬 상태에서 first/last)
    trips = (stop_times.groupby("trip_id", sort=True)
             .agg(pattern_id=("pattern_id", "first"),
                  n_stops=("seq", "size"),
                  start_s=("dep_s", "first"),    # 첫 정류장 arr==dep 실측 [SB§4]
                  end_s=("arr_s", "last"),
                  lineage=("lineage", "first"))
             .reset_index())
    trips["n_stops"] = trips["n_stops"].astype("int16")
    trips["start_s"] = trips["start_s"].astype("int32")
    trips["end_s"] = trips["end_s"].astype("int32")

    # 4) stops — stop 컬럼 표준화 + in_schedule 플래그 (미사용 12 = KTDB 8 + 내고산 4 [SB§8][RS§5])
    stops = pd.DataFrame({
        "stop_id": raw_stop_rows["stop_id"],
        "stop_name": raw_stop_rows["stop_name"],
        "lat": raw_stop_rows["stop_lat"].astype("float64"),
        "lon": raw_stop_rows["stop_lon"].astype("float64"),
        "lineage": _lineage(raw_stop_rows["stop_id"]),
        "in_schedule": raw_stop_rows["stop_id"].isin(set(stop_times["stop_id"])).astype(bool),
    })

    # 5) route_union — 정류장 이름과 좌표로 stop_id 해소 (alias 1건·실패 0 [RS§5])
    resolved, failures, n_alias = normalize.resolve_stops_by_name(
        bus, stops, "정류장명", "위도", "경도")
    meta["join"]["n_alias"] = int(n_alias)
    meta["join"]["n_fail"] = int(len(failures))
    if len(failures):
        # 실패는 산출물 결측으로 남겨 C-S00-B-013이 FAIL과 표본 덤프를 담당하게 한다.
        dbg = vdir / "_debug"
        p = dbg / "join_failures.csv"
        paths.assert_writable(p)
        dbg.mkdir(exist_ok=True)
        failures.to_csv(p, index=False, encoding="utf-8-sig")
    ok = resolved.notna()
    if ok.any():
        stop_lookup = stops.set_index("stop_id")
        d = normalize.haversine_m(
            bus.loc[ok, "위도"].astype(float).values,
            bus.loc[ok, "경도"].astype(float).values,
            stop_lookup.loc[resolved[ok], "lat"].values,
            stop_lookup.loc[resolved[ok], "lon"].values)
        meta["join"]["resolved_max_dist_m"] = float(np.max(np.atleast_1d(d)))
    else:
        meta["join"]["resolved_max_dist_m"] = None
    meta["join"]["resolved_stop_nunique"] = int(resolved.nunique())

    kr_map = {t: raw_bus_route_before.kr_hms_to_sec(t)
              for t in pd.unique(pd.Series(
                  [tok for cell in pd.concat([bus["도착시간들"], bus["출발시간들"]])
                   for tok in raw_bus_route_before.split_time_list(cell)]))}
    route_union = pd.DataFrame({
        "route_name": bus["노선명"],
        "seq": bus["정류장순서"].astype("int16"),
        "원본순서": bus["원본순서"].astype("int16"),
        "stop_id": resolved,
        "도착횟수": bus["도착횟수"].astype("int32"),
        "arr_list_s": [[kr_map[t] for t in raw_bus_route_before.split_time_list(c)]
                       for c in bus["도착시간들"]],
        "dep_list_s": [[kr_map[t] for t in raw_bus_route_before.split_time_list(c)]
                       for c in bus["출발시간들"]],
    })

    # 6) variant_tags — 개명(route_id→pattern_id, base_route_id→base_pattern_id_raw)과
    #    타입 변환만 수행한다. base_pattern_id_raw는 자기참조까지 그대로 보존한다.
    vt = tags.rename(columns={"route_id": "pattern_id",
                              "base_route_id": "base_pattern_id_raw"})[_VT_COLUMNS].copy()
    vt["n_stops"] = vt["n_stops"].astype("int32")
    vt["frequency"] = vt["frequency"].astype("int32")
    vt["is_loop"] = (vt["is_loop"] == "True").astype(bool)
    vt["verified"] = (vt["verified"] == "True").astype(bool)

    out = {
        "stop_times.parquet": _write_parquet(stop_times, vdir / "stop_times.parquet"),
        "trips.parquet": _write_parquet(trips, vdir / "trips.parquet"),
        "stops.parquet": _write_parquet(stops, vdir / "stops.parquet"),
        "route_union.parquet": _write_parquet(route_union, vdir / "route_union.parquet"),
        "variant_tags.parquet": _write_parquet(vt, vdir / "variant_tags.parquet"),
    }
    meta_p = vdir / "ingest_meta.json"
    paths.assert_writable(meta_p)
    with open(meta_p, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    out["ingest_meta.json"] = meta_p
    return out
