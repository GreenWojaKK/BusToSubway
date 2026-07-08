"""시각 파서 3종 — hms24_to_sec / parse_kr_ampm / to_service_s (design.md §2.4).

단일 시각 규약 service_s: 서비스데이 00:00 기준 경과 초(int), 자정 넘김은 24h+로 연장.
결과: 두 스코프 모두 service_s ∈ [4h, 26h) — s00 이후 문자열 시각은 어디에도 없다.
서비스 창 경계는 params.yaml(time.service_min_h / service_max_h) — 코드 리터럴 금지.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time

import paths

# 단위 환산 상수 (임계값 아님 — 물리 단위 정의)
SEC_PER_HOUR = 3600
SEC_PER_MIN = 60

HMS24_RE = re.compile(r"^(\d{1,2}):([0-5]\d):([0-5]\d)$")
KR_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (오전|오후) (\d{1,2}):(\d{2}):(\d{2})$")
# after departure sentinel 2종 (audit/schedule_after.md §4)
SENTINELS = {"0001-01-01": "no_departure", "2025-05-07 0:00": "malformed_single"}

_NOON_H = 12  # 12시간제 정오 (오전12=0시, 오후12=12시 규약의 축)


def _window() -> tuple[int, int]:
    t = paths.load_params()["time"]
    return t["service_min_h"] * SEC_PER_HOUR, t["service_max_h"] * SEC_PER_HOUR


def hms24_to_sec(s: str) -> int:
    """before 계열. zero-pad 없음('7:10:00')·24+시('25:10:01') 허용 → 초.

    불일치 ValueError. strptime('%H:%M:%S') 사용 금지 — 엄격 파서가 한 자리 시에서 실패하고
    24+시를 아예 수용하지 못한다(감사 [SB§4]: zero-pad 73.5%만 2자리, 24시 이상 440행).
    """
    m = HMS24_RE.match(str(s).strip())
    if not m:
        raise ValueError(f"hms24 형식 위반: {s!r}")
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
    total = h * SEC_PER_HOUR + mi * SEC_PER_MIN + se
    lo, hi = _window()
    if not (lo <= total < hi):
        raise ValueError(f"service_s 창 [{lo},{hi}) 밖: {s!r} → {total}")
    return total


def parse_kr_ampm(s: str) -> datetime | None:
    """after 계열. '오전/오후' 12시간제 timestamp 파싱 (오전12=0시, 오후12=12시).

    sentinel이면 None을 반환한다(호출측이 dep_is_sentinel을 기록).
    sentinel도 regex도 아니면 ValueError — 검증 규칙 C-S00-A-004의 '그 외 이형 0'.
    """
    raw = str(s).strip()
    if raw in SENTINELS:
        return None
    m = KR_TS_RE.match(raw)
    if not m:
        raise ValueError(f"오전/오후 timestamp 형식 위반: {s!r}")
    y, mo, d, ampm, h, mi, se = (m.group(1), m.group(2), m.group(3), m.group(4),
                                 int(m.group(5)), int(m.group(6)), int(m.group(7)))
    if not (1 <= h <= _NOON_H):
        raise ValueError(f"12시간제 시 범위 위반: {s!r}")
    if ampm == "오전":
        h24 = 0 if h == _NOON_H else h          # 오전12 = 0시
    else:
        h24 = _NOON_H if h == _NOON_H else h + _NOON_H  # 오후12 = 12시
    return datetime(int(y), int(mo), int(d), h24, mi, se)


def to_service_s(ts: datetime, service_date: date) -> int:
    """(ts − service_date 00:00)의 초. 익일 새벽은 자동으로 24h+ wrap.

    결과가 [service_min_h*3600, service_max_h*3600) 밖이면 ValueError.
    """
    delta = ts - datetime.combine(service_date, time(0))
    sec = int(delta.total_seconds())
    lo, hi = _window()
    if not (lo <= sec < hi):
        raise ValueError(
            f"service_s 창 [{lo},{hi}) 밖: {ts} (service_date={service_date}) → {sec}")
    return sec
