"""s00_ingest / after — 원시 데이터 로딩과 형태 정규화 (design.md §5 s00, stage1_canonical_spec.md §4.2).

이 단계는 원시 파일에서 확인된 기계적 형태 정규화만 수행한다:
utf-8-sig, dtype, 컬럼 표준화(route_id→pattern_id, stop_id 유지),
오전/오후 시각→service_s, sentinel의 플래그화, timestamp 재정렬, '.0' strip(raw_stops 소속).
의미 해석(trip 복원, main 선정 등)은 전부 s01 이후 소속이다.

출력 4종 (행수 체크 대상):
  events.parquet       280,797 — timestamp 재정렬 완료(원본 행 순서 의존 금지 [SA§5.1])
  route_master.parquet  18,106 — (pattern_id, master_seq)→stop_id 1:1 격자.
                        ★ 스키마 주석: master_seq의 결번은 결측이 아니라 '통과 stop 인덱스'다
                        (급행 5001: 165칸 중 21정차 [SA§5.4]). 순번을 "연속 정차 카운터"로
                        가정하는 코드는 전부 틀린다 — dense-rank 재부여 금지.
  stops.parquet          3,224 — '.0' strip을 거친 표준 ID (strip은 io/raw_stops.load_after 소속)
  route_agg.parquet     17,841 — bus_route_after 정규화본 + derived_from_schedule=True
                        (같은 날 로그의 집계본 [SA§6.3] — 독립 검증 근거로 사용하지 않음)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import bts.paths as paths
from bts.io import raw_bus_route_after, raw_schedule_after, raw_stops, timeparse

# 단위 환산 상수 (임계값 아님 — timeparse의 SEC_PER_HOUR와 동위)
SEC_PER_DAY = 24 * timeparse.SEC_PER_HOUR
# 좌표 정규화 자릿수 — stops_after의 14~15자리 부동소수점 잔재를 round(6)로 정리
COORD_DECIMALS = 6


def _service_date(sched: pd.DataFrame):
    """운행 일자(nunique==1, 로더가 assert)를 service date로 해석."""
    return datetime.strptime(sched["운행 일자"].iloc[0], "%Y%m%d").date()


def _build_events(sched: pd.DataFrame) -> pd.DataFrame:
    """오전/오후 파싱 + sentinel 플래그화 + timestamp 재정렬 (audit/schedule_after.md §3~§5).

    - 자정 넘김은 timestamp 날짜부(05-07)로 표현되므로 to_service_s가 자동으로 24h+ wrap.
    - departure sentinel(8,302+1행)은 dep_s=NA + dep_is_sentinel=True로 플래그화 —
      이벤트 기준 시각은 arrival(결측 0)이다 [SA§4].
    - 원본 행 순서는 (OBE,pattern) 기준 64,587 조각으로 파편화 [SA§5.1] —
      (obe_id, pattern_id, arr_ts, master_seq)로 재정렬해 행 순서 의존을 차단한다.
    """
    svc = _service_date(sched)

    # 고유값 단위 파싱(유일 arrival 66,089 / departure 65,892+2) — 공통 파서만 사용
    arr_ts_map = {s: timeparse.parse_kr_ampm(s) for s in sched["arrival_time"].unique()}
    arr_s_map = {s: timeparse.to_service_s(t, svc) for s, t in arr_ts_map.items()}
    dep_ts_map = {s: timeparse.parse_kr_ampm(s) for s in sched["departure_time"].unique()}
    dep_s_map = {s: (None if t is None else timeparse.to_service_s(t, svc))
                 for s, t in dep_ts_map.items()}

    ev = pd.DataFrame({
        # 컬럼 표준화 (design.md §2.1): route_id→pattern_id, stop_id 유지
        "obe_id": sched["OBE_ID"],
        "pattern_id": sched["route_id"],
        "route_name": sched["route_name"],                    # nullable — 이름미상 3 pattern
        "master_seq": sched["버스정류장 순번"].astype("int16"),
        "stop_id": sched["stop_id"],                          # 단축 5자리 표준 ID [SA§6.1]
        "arr_ts": sched["arrival_time"].map(arr_ts_map),
        "arr_s": sched["arrival_time"].map(arr_s_map).astype("int32"),
        "dep_s": sched["departure_time"].map(dep_s_map).astype("Int32"),   # nullable
        "dep_is_sentinel": sched["departure_time"].isin(set(timeparse.SENTINELS)),
        "lat": sched["위도"].astype(float),
        "lon": sched["경도"].astype(float),
        "zone": sched["권역"],
        "admin_dong": sched["행정동"],
        # 스키마 확장 2컬럼: stop_id_raw는 파생 보관(9990 차고지 코드 등 — spec §4.2),
        # service_date는 s01 trip 복원의 그룹 키·trip_uid 날짜부 (spec §6.1)
        "stop_id_raw": sched["stop_id_raw"],
        "service_date": sched["운행 일자"],
    })
    ev = ev.sort_values(["obe_id", "pattern_id", "arr_ts", "master_seq"],
                        kind="mergesort").reset_index(drop=True)
    return ev


def _build_route_master(events: pd.DataFrame) -> pd.DataFrame:
    """(pattern_id, master_seq) → stop_id 1:1 격자 — 18,106 조합 [SA§5.4].

    ★ 결번은 결측이 아니라 '정차 여부와 무관한 경로상 통과 stop 인덱스'다.
    노선 union으로도 1..max가 채워지지 않는 것이 정상(350/351 노선에 빈틈) —
    "연속 정차 카운터" 가정과 dense-rank 재부여를 금지한다.
    """
    rm = (events[["pattern_id", "master_seq", "stop_id"]]
          .drop_duplicates()
          .sort_values(["pattern_id", "master_seq"])
          .reset_index(drop=True))
    return rm


def _build_stops(raw_stop_rows: pd.DataFrame) -> pd.DataFrame:
    """stops_after → stops (즉시 개명 + round(6) 좌표 정규화).

    '.0' float 문자열 strip은 raw_stops.load_after 소속(파일 오귀속 함정 — design.md §13).
    """
    return pd.DataFrame({
        "stop_id": raw_stop_rows["stop_id"],
        "stop_name": raw_stop_rows["stop_name"],
        "lat": raw_stop_rows["stop_lat"].astype(float).round(COORD_DECIMALS),
        "lon": raw_stop_rows["stop_lon"].astype(float).round(COORD_DECIMALS),
        "zone": raw_stop_rows["zone"],
        "admin_dong": raw_stop_rows["admin_dong"],
    })


def _to_service_list(cell: str, wrap_below_s: int, hi_s: int) -> np.ndarray:
    """도착시간들 셀(파이프 구분, 2포맷 겸용) → service_s int32 배열.

    after의 심야는 0시 wrap 표기 [RS§7] — 서비스 창 시작(4h) 미만 토큰은 +86400.
    """
    out = []
    for tok in cell.split("|"):
        sec = raw_bus_route_after.parse_time_token(tok)
        if sec < wrap_below_s:
            sec += SEC_PER_DAY
        if not (wrap_below_s <= sec < hi_s):
            raise ValueError(f"service_s 창 [{wrap_below_s},{hi_s}) 밖 토큰: {tok!r} → {sec}")
        out.append(sec)
    return np.asarray(out, dtype="int32")


def _build_route_agg(br: pd.DataFrame) -> pd.DataFrame:
    """bus_route_after 정규화본 — 개명 + 시각 리스트 초 변환 + 파생 표식.

    derived_from_schedule=True: 같은 날 로그의 집계본(17,840/17,841 일치 실측 [SA§6.3])
    이므로 독립 검증 근거로 사용하지 않는다.
    s00 이후 문자열 시각은 어디에도 존재하지 않는다(design.md §2.4).
    """
    t = paths.load_params()["time"]
    lo = t["service_min_h"] * timeparse.SEC_PER_HOUR
    hi = t["service_max_h"] * timeparse.SEC_PER_HOUR
    return pd.DataFrame({
        "pattern_id": br["route_id"],
        "route_name": br["route_name"],
        "seq": br["stop_sequence"].astype("int16"),
        "stop_id": br["stop_id"],
        "stop_name": br["stop_name"],
        "lat": br["stop_lat"].astype(float).round(COORD_DECIMALS),
        "lon": br["stop_lon"].astype(float).round(COORD_DECIMALS),
        "zone": br["권역"],
        "admin_dong": br["행정동"],
        "도착횟수": br["도착횟수"].astype("int32"),
        "arr_list_s": br["도착시간들"].map(lambda c: _to_service_list(c, lo, hi)),
        "derived_from_schedule": True,
    })


def build(inputs, params: dict, vdir: Path) -> dict[str, Path]:
    """s00_ingest/after 빌드 — 로더 4종 → 형태 정규화 → parquet 4종."""
    sched = raw_schedule_after.load()      # 원시 입력 형태 확인 [SA§8]
    raw_stop_rows = raw_stops.load_after()         # '.0' strip 포함 [RS§5]
    br = raw_bus_route_after.load()        # 시각 2포맷 존재 검증 포함 [RS§7]

    events = _build_events(sched)
    route_master = _build_route_master(events)
    stops = _build_stops(raw_stop_rows)
    route_agg = _build_route_agg(br)

    out = {}
    for name, df in [("events.parquet", events),
                     ("route_master.parquet", route_master),
                     ("stops.parquet", stops),
                     ("route_agg.parquet", route_agg)]:
        p = vdir / name
        df.to_parquet(p, index=False)
        out[name] = p
    return out
