"""s02_place / after 필수 검증 — C-S02-A-001~005·007·008, P-S02-A-001.

구현은 s02_common(양 스코프 공통 발번). after는 C-S02-*-006(이름 분포) 대응 체크 없음
(감사 미실측 — spec §4.5 주), DIFF 없음(기준값 부재가 검증 규칙 — 크로스 스코프 비교 금지).
"""
from bts.checks.contracts import s02_common


def run(ctx) -> list:
    return s02_common.run_scope(ctx, "after")
