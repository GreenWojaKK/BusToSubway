"""선행 기준값 비교 판정기 (verification.md §2.3, design.md §6.2).

3값 판정: MATCH → EXPLAINED(대장 등재 AND observed == 등재 measured) → UNEXPLAINED.
등재 measured에서 재이탈하면 EXPLAINED가 성립하지 않는다(설명 값 재확인).
UNEXPLAINED는 게시를 막지 않되(SIGNAL) 조사 메모가 자동 생성된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import bts.paths as paths
from bts.checks.core import CheckResult

_INVESTIGATION_NOTE_RE = re.compile(r"^DIFF-(\d{4})-(.+)\.md$")
_STUB_RE = _INVESTIGATION_NOTE_RE


@dataclass
class DocumentedDifference:
    """known_deviations.yaml 1행 — measured 고정값 포함."""
    id: str
    check: str
    prior: object
    measured: object
    status: str          # hypothesis | confirmed
    doc: str
    note: str = ""


def load_reference_values(path: Path | None = None) -> dict:
    import yaml
    p = path or (paths.BASELINE_DIR / "baseline.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


KD = DocumentedDifference


def load_known_deviations(path: Path | None = None) -> list[DocumentedDifference]:
    import yaml
    p = path or (paths.BASELINE_DIR / "known_deviations.yaml")
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    fields = DocumentedDifference.__dataclass_fields__
    return [DocumentedDifference(**{k: v for k, v in row.items() if k in fields})
            for row in raw.get("deviations", [])]


def reference_value(reference_values: dict, key: str):
    """'before.canonical.rows' 형태의 점 경로 조회. 부재 시 KeyError."""
    node = reference_values
    for part in key.split("."):
        node = node[part]
    return node


def _close(a, b, tol) -> bool:
    try:
        return abs(a - b) <= tol
    except TypeError:
        return a == b


def _slug(metric: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", metric).strip("-")


def make_investigation_note(metric: str, cid: str, observed, expected,
                            stub_dir: Path | None = None) -> Path:
    """docs/internal/investigations/DIFF-NNNN-<metric>.md 조사 메모 자동 생성.

    같은 metric의 기존 조사 메모가 있으면 재사용(중복 발번 방지).
    """
    note_dir = stub_dir or (paths.ROOT / paths.load_params()["diff"]["stub_dir"])
    note_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(metric)
    existing_numbers = []
    for note_path in note_dir.glob("DIFF-*.md"):
        match = _INVESTIGATION_NOTE_RE.match(note_path.name)
        if match:
            if match.group(2) == slug:
                return note_path             # 기존 조사 메모 재사용
            existing_numbers.append(int(match.group(1)))
    next_number = max(existing_numbers, default=0) + 1
    note_path = note_dir / f"DIFF-{next_number:04d}-{slug}.md"
    note_path.write_text(
        f"""# DIFF-{next_number:04d} — {metric} (UNEXPLAINED)

- 상태: **미설명** (자동 생성 조사 메모 — checks/diff.py)
- 생성일: {date.today().isoformat()}
- check_id: `{cid}`
- baseline(선행 구현): `{expected}`
- observed: `{observed}`

## 1차 분기 (verification.md §3.1)

- [ ] raw sha256 기준값 대조 (data_drift?)
- [ ] 상류 manifest 버전/해시 변화 (upstream_regression?)
- [ ] params_hash 대조 (param_sensitivity?)
- [ ] 규약 메타(M, universe, 방향 union 등) 대조 (convention_mismatch?)
- [ ] 기준값 자체의 노후·내부 모순 (baseline_stale?)

## 설명

(원인 설명이 산출물이다. 종결 = (a) 코드 수정 후 MATCH,
(b) 근거와 함께 known_deviations 등재 — status: hypothesis|confirmed 구분.)
""",
        encoding="utf-8")
    return note_path


make_stub = make_investigation_note


def judge(cid: str, observed, reference_key: str, tol=None, metric: str | None = None,
          source: str = "prior_baseline", baseline: dict | None = None,
          documented_differences: list[DocumentedDifference] | None = None,
          stub_dir: Path | None = None,
          make_stub_on_unexplained: bool = True, **legacy_kwargs) -> CheckResult:
    """DIFF 3값 판정 + measured 값 재확인 + 조사 메모 자동 생성.

    baseline/documented_differences/stub_dir 인자는 테스트 주입용 — 기본은 기준 입력 묶음에서 로드.
    """
    if documented_differences is None and "kds" in legacy_kwargs:
        documented_differences = legacy_kwargs.pop("kds")
    if legacy_kwargs:
        unexpected = ", ".join(sorted(legacy_kwargs))
        raise TypeError(f"unexpected keyword argument(s): {unexpected}")
    if tol is None:
        tol = paths.load_params()["diff"]["default_tol"]
    baseline = baseline if baseline is not None else load_reference_values()
    documented_differences = (documented_differences if documented_differences is not None
                              else load_known_deviations())
    metric = metric or reference_key
    expected = reference_value(baseline, reference_key)

    if _close(observed, expected, tol):
        status, note = "MATCH", ""
    else:
        documented = next((item for item in documented_differences if item.check == cid), None)
        if documented is not None and observed == documented.measured:
            status = "EXPLAINED"
            note = f"{documented.id} ({documented.status}) — {documented.doc}"
        elif documented is not None:
            # 등재 measured 재이탈 — 설명 값 불일치: 자동 강등
            status = "UNEXPLAINED"
            note = (f"{documented.id} 등재 measured={documented.measured}에서 재이탈(observed={observed}) "
                    f"— EXPLAINED 자동 강등(설명 값 재확인)")
        else:
            status, note = "UNEXPLAINED", "대장 미등재 편차"

    r = CheckResult(
        check_id=cid, check_class="DIFF", severity="SIGNAL", status=status,
        observed=observed, expected=expected, source=source,
        failure_means=["convention_mismatch", "baseline_stale", "logic_bug",
                       "param_sensitivity"],
        note=note)
    if status == "UNEXPLAINED" and make_stub_on_unexplained:
        note_path = make_investigation_note(metric, cid, observed, expected, stub_dir=stub_dir)
        r.action_hint = f"조사 메모: {note_path}"
    return r
