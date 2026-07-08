"""s02_place / before 필수 검증 — C-S02-B-001~008, P-S02-B-001, D-S02-B-001~003.

구현은 s02_common(양 스코프 공통 발번) — verification.md §5.5, stage2_place_hub_spec.md §4.5.
D-S02-B-001~003은 첫 실행에서 UNEXPLAINED가 예상 상태다(§4.6 사전 캘리브레이션) —
설명 후 known_deviations 등재로 EXPLAINED 전환이 설계된 경로.
"""
from checks.contracts import s02_common


def run(ctx) -> list:
    return s02_common.run_scope(ctx, "before")
