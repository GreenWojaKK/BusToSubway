"""s01_canonical — 대표 노선 패턴 구성 (frame #2, design.md §5 s01).

before: variant_tags를 붙이는 경로 — canonical 379·backbone 253·분류표 487·trip 연결 7,625.
after : trip 복원 경로 — trip 4,524·registry 351/184 (별도 모듈).

이 패키지 루트는 스코프 공용 API만 노출한다:
- get_rules(era): route_class_rules.<era>.yaml 로더 — era 없는 규칙 조회는 허용하지 않는다
  (design.md §7.2: "era 키 없는 규칙 조회는 KeyError다". before 규칙의 after 적용은
  expect_count가 즉시 반증한다 — after 4자리 26개 실측).
"""
from __future__ import annotations

import yaml

import bts.paths as paths


def get_rules(era: str) -> dict:
    """route_class 판정 규칙 조회 — 유일한 규칙 조회 API (design.md §7.2).

    규칙은 코드가 아니라 데이터다: src/bts/config/route_class_rules.<era>.yaml.
    미지 era(파일 부재) 또는 파일 내부 era 필드 불일치는 KeyError로 처리한다.
    """
    p = paths.CONFIG / f"route_class_rules.{era}.yaml"
    if not p.exists():
        raise KeyError(
            f"route_class_rules: 미지 era '{era}' — era 키 없는 규칙 조회는 불가 (design.md §7.2)")
    with open(p, encoding="utf-8") as f:
        rules = yaml.safe_load(f)
    if rules.get("era") != era:
        raise KeyError(
            f"route_class_rules.{era}.yaml의 era 필드({rules.get('era')})가 요청 era와 불일치")
    return rules
