"""after 이벤트 로그를 trip 단위로 나누기 (audit/schedule_after.md §5.1).

분할 규칙:
  ① (service_date, obe_id, pattern_id) 그룹  — 1,055 그룹 [SA§5.1]
  ② 그룹 내 arr_ts 정렬 — 원본 행 순서 사용 금지(파일은 64,587 조각으로 파편화 [SA§5.1])
  ③ master_seq[i] <= master_seq[i-1] 지점 절단 → 경계 3,469 + 그룹 1,055 == 4,524 trips
  ④ trip_uid = f"{date}_{obe}_{pattern_id}_{trip_no:03d}"

★ 복원된 trip은 왕복(회차) 단위다 [SA§5.3] — 순번이 회차점을 지나 복귀 구간까지 계속
증가하며, 복귀 구간은 방향별로 다른 stop을 쓴다("같은 stop 정차 = 같은 방향").
편도(leg) 분리 알고리즘은 미확정(ADR-002)이라 이 스테이지는 trip_legs를 만들지 않는다.

플래그 3종:
  is_partial        시작 seq > 전역 최소 관측 seq(=파일 전체 min master_seq).
                    판별 근거: 감사 §5.5의 3개 수치 — 28.3%(1,279),
                    변형코드별 비율(끝0 26.5%/끝1 44.9%), 2100 양산 100% — 를 전부 재현하는
                    기준은 전역 최소뿐이다(pattern별 min 기준 21.9%·route_name별 min 기준
                    24.6%는 재현 실패). "노선 최소 관측 seq"라는 감사 §5.5의 표현은 전역
                    최소(=1)로 읽어야 실측과 정합한다.
  crosses_midnight  trip 종료 arr_s가 서비스데이 24h를 넘는 trip — 37 trips [SA§3]
  boundary_clipped  경계 클리핑 pattern(양산 광역 — 규칙은 route_class_rules.after.yaml의
                    scope_flag: boundary_clipped 행에서 온다. 코드에 prefix 리터럴 금지)

partial_reason (설명용 라벨 — 판정 아님; 감사 §5.5의 원인 3종을 기술적으로 구분):
  none           is_partial=False
  boundary_clip  is_partial & boundary_clipped (광역노선 경계 클리핑 — 2100은 100%)
  first_service  is_partial & 그룹 첫 trip(trip_no==1) — 새벽 첫차 중간 기점 출발 근사
  detect_gap     그 외 (OBE 검지 누락 — 시작 seq 3~8이 흔함)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bts.io import timeparse

# 그룹/trip 자연키 (audit/schedule_after.md §5.1, §6.4)
GROUP_KEYS = ["service_date", "obe_id", "pattern_id"]
TRIP_KEYS = GROUP_KEYS + ["trip_no"]
# partial_reason enum (stage1_canonical_spec.md §6.1)
PARTIAL_REASONS = ("none", "first_service", "boundary_clip", "detect_gap")

# 단위 환산 상수 (임계값 아님 — s00_ingest.after의 SEC_PER_DAY와 동위)
SEC_PER_DAY = 24 * timeparse.SEC_PER_HOUR
# trip_no 자릿수 — trip_uid 규약 {trip_no:03d} (stage1_canonical_spec.md §6.1)
TRIP_NO_DIGITS = 3


def reconstruct_trips(events: pd.DataFrame,
                      clip_prefixes: tuple[str, ...] = ()
                      ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """이벤트 로그 → (trips, trip_events).

    events: s00 events.parquet 스키마(service_date, obe_id, pattern_id, master_seq,
            arr_ts, arr_s, route_name, ...). 행 순서 무관 — 내부에서 재정렬한다.
    clip_prefixes: boundary_clipped 판정 pattern_id 접두(예: 양산 광역) —
            route_class_rules.after.yaml의 scope_flag 행에서 호출측이 추출해 넘긴다.

    반환:
      trips        trip 1행: trip_uid(PK), 자연키, 시각·순번 요약, 플래그 3종 + partial_reason
      trip_events  events 전행 + trip_no/trip_uid 부착 (행수 보존 — 조용한 탈락 0)
    """
    # ① 재정렬 — 원본 행 순서 의존 금지 [SA§5.1]. 동시각 tie(농소차고지 4건 [SA§6.4])는
    #    master_seq 오름차순으로 안정 정렬해 거짓 경계를 만들지 않는다.
    ev = (events.sort_values(GROUP_KEYS + ["arr_ts", "master_seq"], kind="mergesort")
                .reset_index(drop=True))

    # ② 그룹 내 분할: master_seq가 직전 이하로 떨어지는 지점 [SA§5.1]
    prev_seq = ev.groupby(GROUP_KEYS, sort=False)["master_seq"].shift()
    new_trip = prev_seq.isna() | (ev["master_seq"] <= prev_seq)
    ev["trip_no"] = (new_trip.groupby([ev[k] for k in GROUP_KEYS])
                             .cumsum().astype("int16"))

    # ③ trip_uid 발급 — {date}_{obe}_{pattern_id}_{trip_no:03d}
    ev["trip_uid"] = (ev["service_date"] + "_" + ev["obe_id"] + "_" + ev["pattern_id"]
                      + "_" + ev["trip_no"].astype(str).str.zfill(TRIP_NO_DIGITS))

    # ④ trip 요약 (그룹 내 arr 정렬 상태이므로 first/last가 시점·종점)
    grp = ev.groupby(TRIP_KEYS, sort=False)
    trips = grp.agg(
        trip_uid=("trip_uid", "first"),
        route_name=("route_name", "first"),      # 이름미상 3 pattern은 NaN 유지
        n_events=("master_seq", "size"),
        start_seq=("master_seq", "first"),
        end_seq=("master_seq", "last"),
        start_s=("arr_s", "first"),
        end_s=("arr_s", "last"),
    ).reset_index()
    trips = trips.astype({"n_events": "int16", "start_seq": "int16", "end_seq": "int16",
                          "start_s": "int32", "end_s": "int32"})

    # ⑤ 플래그 — is_partial: 전역 최소 관측 seq 기준 (모듈 docstring의 재현 근거 참조)
    global_min_seq = int(ev["master_seq"].min())
    trips["is_partial"] = trips["start_seq"] > global_min_seq
    # crosses_midnight: 종료 arr_s가 서비스데이 24h 초과 (trip 내 arr 비감소이므로 end가 최대)
    trips["crosses_midnight"] = trips["end_s"] >= SEC_PER_DAY
    # boundary_clipped: 규칙 yaml에서 온 접두(코드 리터럴 금지)
    if clip_prefixes:
        trips["boundary_clipped"] = trips["pattern_id"].str.startswith(tuple(clip_prefixes))
    else:
        trips["boundary_clipped"] = False

    # ⑥ partial_reason — 설명용 라벨(판정 아님): none → boundary_clip → first_service → detect_gap
    trips["partial_reason"] = np.select(
        [~trips["is_partial"],
         trips["boundary_clipped"],
         trips["trip_no"] == 1],
        [PARTIAL_REASONS[0], PARTIAL_REASONS[2], PARTIAL_REASONS[1]],
        default=PARTIAL_REASONS[3])

    # 열 순서 정리 (trip_uid가 PK)
    trips = trips[["trip_uid"] + TRIP_KEYS[:-1] + ["trip_no", "route_name",
                   "n_events", "start_seq", "end_seq", "start_s", "end_s",
                   "is_partial", "partial_reason", "crosses_midnight", "boundary_clipped"]]
    return trips, ev
