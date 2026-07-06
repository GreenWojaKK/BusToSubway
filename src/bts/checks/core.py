"""CheckResult · 선언 primitive 어휘 · 체크 실행기 · checks.json 기록 (verification.md §1, §3).

primitive 어휘 10개(§1.3)만 제공한다 — 이것으로 표현 안 되는 것만 파이썬 callable.
accounting은 1급 어휘다: 전수 회계가 "조용히 사라지는 행"을 구조적으로 차단한다.
모든 FAIL은 표본을 남긴다(_debug/) — 덤프 없는 FAIL은 하네스 결함으로 취급(§7 규율 6).
"""
from __future__ import annotations

import dataclasses
import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import bts.paths as paths

SEVERITY = {"CONTRACT": "BLOCK", "PHYSICAL": "WARN", "DIFF": "SIGNAL"}

# failure_means 6종 (verification.md §3.1)
FAILURE_MEANS = ("data_drift", "loader_bug", "upstream_regression",
                 "logic_bug", "param_sensitivity", "convention_mismatch",
                 "baseline_stale")

_DEFAULT_MEANS = {
    "CONTRACT": ["loader_bug", "upstream_regression", "logic_bug"],
    "PHYSICAL": ["logic_bug"],
    "DIFF": ["convention_mismatch", "baseline_stale", "logic_bug"],
}


@dataclass
class CheckResult:
    """verification.md §1.2 스키마 그대로."""
    check_id: str            # "C-S01-B-004"
    check_class: str         # CONTRACT | PHYSICAL | DIFF
    severity: str            # BLOCK | WARN | SIGNAL
    status: str              # PASS | FAIL | SKIP | (DIFF: MATCH | EXPLAINED | UNEXPLAINED)
    observed: object
    expected: object
    source: str              # 감사 출처 (예: "audit/variant_tags.md §6")
    failure_means: list = field(default_factory=list)
    action_hint: str = ""
    sample_path: str | None = None   # _debug/<check_id>_sample.csv (상한 200행)
    positive_control: bool = False   # "반드시 검출해야 통과" 체크
    note: str = ""

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["observed"] = _jsonable(d["observed"])
        d["expected"] = _jsonable(d["expected"])
        return d

    @property
    def failed(self) -> bool:
        return self.status in ("FAIL", "UNEXPLAINED")


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return repr(v)


def _mk(cid: str, cls: str, ok: bool, observed, expected, source: str,
        positive_control: bool = False, note: str = "",
        failure_means=None) -> CheckResult:
    if cls not in SEVERITY:
        raise ValueError(f"미지의 체크 클래스: {cls}")
    return CheckResult(
        check_id=cid, check_class=cls, severity=SEVERITY[cls],
        status="PASS" if ok else "FAIL",
        observed=observed, expected=expected, source=source,
        failure_means=list(failure_means or _DEFAULT_MEANS[cls]),
        positive_control=positive_control, note=note)


# ── 범용 술어 ────────────────────────────────────────────────────────────────
def check_eq(cid, cls, observed, expected, source, **kw) -> CheckResult:
    return _mk(cid, cls, observed == expected, observed, expected, source, **kw)


def check_true(cid, cls, cond, observed, expected, source, **kw) -> CheckResult:
    return _mk(cid, cls, bool(cond), observed, expected, source, **kw)


# ── 선언 primitive 어휘 (verification.md §1.3 — 이것만, 과설계 방지) ─────────
def row_count(cid, cls, df, expected, source, **kw) -> CheckResult:
    return _mk(cid, cls, len(df) == expected, len(df), expected, source, **kw)


def nunique(cid, cls, series, expected, source, **kw) -> CheckResult:
    n = series.nunique()
    return _mk(cid, cls, n == expected, n, expected, source, **kw)


def unique_key(cid, cls, df, cols, source, **kw) -> CheckResult:
    """복합키 유일성. observed = 중복 행 수 (0이 기대)."""
    dup = int(df.duplicated(subset=list(cols)).sum())
    return _mk(cid, cls, dup == 0, {"dup_rows": dup, "key": list(cols)},
               {"dup_rows": 0}, source, **kw)


def regex_all(cid, cls, series, pattern, source, **kw) -> CheckResult:
    """전행 정규식 일치. observed = 불일치 행 수."""
    bad = int((~series.astype(str).str.fullmatch(pattern)).sum())
    return _mk(cid, cls, bad == 0, {"mismatch_rows": bad, "pattern": pattern},
               {"mismatch_rows": 0}, source, **kw)


def in_enum(cid, cls, series, allowed, source, **kw) -> CheckResult:
    """enum 검증. observed = 미지값 목록."""
    unknown = sorted(set(series.dropna().unique()) - set(allowed))
    return _mk(cid, cls, not unknown, {"unknown_values": unknown},
               {"unknown_values": []}, source, **kw)


def value_range(cid, cls, series, lo, hi, source, right_open=False, **kw) -> CheckResult:
    """값 범위. right_open=True면 [lo, hi)."""
    s = series.dropna()
    bad = int(((s < lo) | (s >= hi if right_open else s > hi)).sum())
    return _mk(cid, cls, bad == 0,
               {"out_of_range_rows": bad, "lo": lo, "hi": hi},
               {"out_of_range_rows": 0}, source, **kw)


def fk_subset(cid, cls, child, parent, source, **kw) -> CheckResult:
    """FK ⊆ PK. observed = dangling 값 수."""
    dangling = sorted(set(child.dropna().unique()) - set(parent.unique()))
    return _mk(cid, cls, not dangling,
               {"dangling_count": len(dangling), "sample": dangling[:10]},
               {"dangling_count": 0}, source, **kw)


def functional(cid, cls, df, a, b, source, **kw) -> CheckResult:
    """A→B 함수적 의존. observed = 위반 A값 수."""
    viol = df.groupby(a)[b].nunique()
    bad = int((viol > 1).sum())
    return _mk(cid, cls, bad == 0, {"violating_keys": bad, "map": f"{a}->{b}"},
               {"violating_keys": 0}, source, **kw)


def accounting(cid, parts: dict, total: int, source, cls: str = "CONTRACT", **kw) -> CheckResult:
    """전수 회계: sum(parts.values()) == total.

    observed에 parts 분해를 그대로 기록 — 실패 시 어느 항이 새는지 즉시 판독.
    """
    s = sum(parts.values())
    return _mk(cid, cls, s == total,
               {"parts": dict(parts), "sum": s},
               {"total": total}, source, **kw)


def monotonic(cid, cls, df, group_cols, col, source, strict=True, **kw) -> CheckResult:
    """그룹 내 단조 (기본 strict). observed = 위반 행 수."""
    diffs = df.groupby(list(group_cols))[col].diff().dropna()
    bad = int((diffs <= 0).sum() if strict else (diffs < 0).sum())
    return _mk(cid, cls, bad == 0,
               {"violations": bad, "col": col, "strict": strict},
               {"violations": 0}, source, **kw)


# ── 표본 덤프 ────────────────────────────────────────────────────────────────
def dump_sample(vdir: Path, cid: str, df_violations, limit: int | None = None) -> str:
    """_debug/<check_id>_sample.csv (상한 params.manifest.debug_sample_limit행)."""
    limit = limit or paths.load_params()["manifest"]["debug_sample_limit"]
    dbg = Path(vdir) / "_debug"
    p = dbg / f"{cid}_sample.csv"
    paths.assert_writable(p)
    dbg.mkdir(exist_ok=True)
    df_violations.head(limit).to_csv(p, index=False, encoding="utf-8-sig")
    return str(p)


# ── 실행기 ──────────────────────────────────────────────────────────────────
class Ctx:
    """체크 함수에 주어지는 유일한 이름공간 (바인딩 모호성 제거).

    input_df는 registry 선언 입력만 접근을 허용한다 — 입력 화이트리스트의 사후 강제.
    """

    def __init__(self, vdir: Path, params: dict, scope: str, inputs=None):
        self.vdir = Path(vdir)
        self.params = params
        self.scope = scope
        self.inputs = inputs            # manifest.ResolvedInputs | None
        self._cache: dict = {}

    def _load(self, p: Path):
        if p not in self._cache:
            if p.suffix == ".parquet":
                import pandas as pd
                self._cache[p] = pd.read_parquet(p)
            elif p.suffix == ".csv":
                import pandas as pd
                self._cache[p] = pd.read_csv(p, encoding="utf-8-sig", dtype=str)
            else:
                raise ValueError(f"지원하지 않는 산출물 포맷: {p}")
        return self._cache[p]

    def df(self, filename: str):
        """당해 스테이지 vdir 내 산출물 로드(캐시)."""
        return self._load(self.vdir / filename)

    def input_df(self, stage: str, filename: str):
        """선언된 상류 입력만 로드 — 화이트리스트 밖 접근은 거부."""
        if self.inputs is None:
            raise paths.PathError("이 컨텍스트에는 선언된 입력이 없다")
        key = f"{stage}/{self.scope}"
        if key not in self.inputs.artifacts:
            raise paths.PathError(
                f"입력 화이트리스트 위반: {stage}는 이 스테이지의 선언 입력이 아니다")
        version = self.inputs.artifacts[key]["version"]
        return self._load(paths.artifact_dir(stage, self.scope, version) / filename)


def _fill_action_hint(r: CheckResult) -> None:
    """엔진의 1차 분기 자동 채움 (verification.md §3.4 triage)."""
    if not r.failed or r.action_hint:
        return
    if r.check_class == "CONTRACT":
        r.action_hint = ("raw sha256 대조: 일치→로더/코드 수정, 불일치→data_drift 에스컬레이션. "
                         "상류 스테이지가 있으면 상류 manifest 버전 변화로 이분.")
    elif r.check_class == "PHYSICAL":
        r.action_hint = ("위반 행 lineage 분포 확인: lineage 예외(ACC0 등)면 검증 규칙에 예외 등재, "
                         "아니면 수정, 수용이면 --ack.")
    else:
        r.action_hint = "params/메타 대조 후 조사 메모 작성 → 코드 수정 or known_deviations 등재."


def run_stage_checks(stage: str, scope: str, vdir: Path, params: dict,
                     inputs=None, checks_ref=None) -> list[CheckResult]:
    """스테이지 체크 모듈 실행. checks_ref: dotted 모듈명 | callable | None.

    체크 모듈 규약: `run(ctx) -> list[CheckResult]` 함수를 노출한다.
    """
    if checks_ref is None:
        from bts.run import REGISTRY
        checks_ref = REGISTRY[stage].checks.get(scope)
    ctx = Ctx(vdir, params, scope, inputs)
    if checks_ref is None:
        return []
    if callable(checks_ref):
        results = checks_ref(ctx)
    else:
        mod = importlib.import_module(checks_ref)
        results = mod.run(ctx)
    for r in results:
        _fill_action_hint(r)
    return list(results)


def summarize(results: list[CheckResult]) -> dict:
    """manifest.checks_summary 형태 집계."""
    required_checks = [r for r in results if r.check_class == "CONTRACT"]
    sanity_checks = [r for r in results if r.check_class == "PHYSICAL"]
    baseline_checks = [r for r in results if r.check_class == "DIFF"]
    return {
        "CONTRACT": "pass" if all(r.status == "PASS" for r in required_checks) else "fail",
        # skip을 요약에 상시 노출 — 게이트에 없는 제3상태(비활성 체크)가 요약 레벨에서
        # 보이지 않으면 사실상 통과로 오독된다 (검증 라운드 1 지적)
        "PHYSICAL": {"pass": sum(r.status == "PASS" for r in sanity_checks),
                     "fail": sum(r.status == "FAIL" for r in sanity_checks),
                     "skip": sum(r.status == "SKIP" for r in sanity_checks)},
        "DIFF": {"match": sum(r.status == "MATCH" for r in baseline_checks),
                 "explained": sum(r.status == "EXPLAINED" for r in baseline_checks),
                 "unexplained": sum(r.status == "UNEXPLAINED" for r in baseline_checks)},
    }


def write_checks_json(vdir: Path, results: list[CheckResult]) -> None:
    p = Path(vdir) / "checks.json"
    paths.assert_writable(p)
    with open(p, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)
