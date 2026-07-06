"""규범 어휘 금칙어 lint (design.md §8 — 기술/판정 분리).

컬럼명·지표 사전에 판정(normative) 어휘를 등재할 수 없다.
"직행불가 많음 ≠ 노선 비합리" — 산출물은 기술(descriptive) 문장만 담는다.
금칙어 목록은 params.yaml(lint.forbidden_terms) — 코드 리터럴 금지.
"""
from __future__ import annotations

from typing import Iterable

import bts.paths as paths
from bts.checks.core import CheckResult


def forbidden_terms() -> list[str]:
    return list(paths.load_params()["lint"]["forbidden_terms"])


def find_forbidden(names: Iterable[str], terms: list[str] | None = None) -> list[tuple[str, str]]:
    """이름 목록에서 금칙어 검출 — (이름, 걸린 금칙어) 쌍 목록."""
    terms = terms if terms is not None else forbidden_terms()
    hits = []
    for name in names:
        low = str(name).lower()
        for t in terms:
            if t.lower() in low:
                hits.append((str(name), t))
    return hits


def check_names(cid: str, names: Iterable[str], source: str,
                terms: list[str] | None = None) -> CheckResult:
    """컬럼명/지표명 금칙어 체크 — CONTRACT(BLOCK) 등급."""
    hits = find_forbidden(names, terms)
    return CheckResult(
        check_id=cid, check_class="CONTRACT", severity="BLOCK",
        status="PASS" if not hits else "FAIL",
        observed={"violations": hits}, expected={"violations": []},
        source=source, failure_means=["logic_bug"],
        note="규범 어휘는 컬럼명·지표 사전에 등재 불가 (design.md §8)")
