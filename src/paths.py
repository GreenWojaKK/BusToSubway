"""경로 단일 정의 + 출력 위치 규칙 (design.md §3.1).

이 모듈이 유일한 경로 진실이다. 다른 모듈은 반드시
``import paths`` 후 ``paths.ARTIFACTS`` 형태로 참조한다
(``from paths import ARTIFACTS`` 금지 — 테스트의 monkeypatch가 깨진다).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path

# ── 경로 상수 ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]     # BTS 루트 (하드코딩 금지 — 위치에서 유도)
DATA = ROOT / "data"
REFERENCE = ROOT / "reference"
ARTIFACTS = ROOT / "artifacts"
RUNS = ROOT / "runs"
DOCS = ROOT / "docs"
CONFIG = Path(__file__).resolve().parent / "config"
BASELINE_DIR = REFERENCE / "prior_baseline"

_VERSION_RE = re.compile(r"^v(\d+)$")


# ── 예외 ────────────────────────────────────────────────────────────────────
class PathError(Exception):
    """경로 계층의 기반 예외."""


class WriteViolation(PathError):
    """출력 위치 규칙(design.md §3.1) 위반."""


class UpstreamMissing(PathError):
    """상류 산출물 부재 — _latest 해석 실패 (exit 5 사유)."""


class UpstreamCorrupt(PathError):
    """상류 산출물 해시 불일치 (exit 5 사유)."""


# ── params 로더 (config는 해시되는 입력) ─────────────────────────────────────
_params_cache: dict | None = None


def load_params(refresh: bool = False) -> dict:
    """config/params.yaml 로드(캐시). 모든 임계값의 유일한 출처."""
    global _params_cache
    if _params_cache is None or refresh:
        import yaml
        with open(CONFIG / "params.yaml", encoding="utf-8") as f:
            _params_cache = yaml.safe_load(f)
    return _params_cache


# ── raw 접근 ────────────────────────────────────────────────────────────────
def raw_path(name: str) -> Path:
    """data/ 또는 reference/ 안의 raw 파일 경로. 반환 전 존재 확인."""
    for base in (DATA, REFERENCE):
        p = base / name
        if p.exists():
            return p
    raise FileNotFoundError(f"raw 파일을 찾을 수 없다: {name} (data/, reference/ 탐색)")


# ── 버전 디렉터리 ────────────────────────────────────────────────────────────
def _scope_dir(stage: str, scope: str) -> Path:
    return ARTIFACTS / stage / scope


def latest_version(stage: str, scope: str) -> str:
    """_latest.json 해석. 없으면 UpstreamMissing."""
    latest = _scope_dir(stage, scope) / "_latest.json"
    if not latest.exists():
        raise UpstreamMissing(
            f"게시본 부재: {stage}/{scope} — _latest.json 없음 (상류 미실행)")
    with open(latest, encoding="utf-8") as f:
        return json.load(f)["version"]


def artifact_dir(stage: str, scope: str, version: str | None = None) -> Path:
    """산출물 버전 디렉터리. version=None이면 _latest.json 해석."""
    if version is None:
        version = latest_version(stage, scope)
    vdir = _scope_dir(stage, scope) / version
    if not vdir.exists():
        raise UpstreamMissing(f"버전 디렉터리 부재: {stage}/{scope}/{version}")
    return vdir


def new_version_dir(stage: str, scope: str) -> Path:
    """artifacts/<stage>/<scope>/vNNN — 다음 번호로 생성(빌드 중 임시, 게시 전 상태)."""
    digits = load_params()["manifest"]["version_digits"]
    sdir = _scope_dir(stage, scope)
    existing = []
    if sdir.exists():
        for d in sdir.iterdir():
            m = _VERSION_RE.match(d.name.split("-")[0])
            if d.is_dir() and m:
                existing.append(int(m.group(1)))
    nxt = max(existing, default=0) + 1
    vdir = sdir / f"v{nxt:0{digits}d}"
    vdir.mkdir(parents=True, exist_ok=False)
    return vdir


def mark_rejected(vdir: Path) -> Path:
    """vNNN → vNNN-rejected 개명 (포렌식 보존 — design.md §4.3)."""
    target = vdir.with_name(vdir.name + "-rejected")
    n = 2
    while target.exists():
        target = vdir.with_name(f"{vdir.name}-rejected-{n}")
        n += 1
    os.replace(vdir, target)
    return target


# ── 출력 위치 규칙 ──────────────────────────────────────────────────────────
_active_build_dir: Path | None = None
_publish_allowed: bool = False


@contextlib.contextmanager
def build_context(vdir: Path):
    """스테이지 러너의 빌드 구간 — 이 안에서만 해당 vdir 쓰기가 허용된다."""
    global _active_build_dir
    prev = _active_build_dir
    _active_build_dir = vdir.resolve()
    try:
        yield vdir
    finally:
        _active_build_dir = prev


@contextlib.contextmanager
def publish_context():
    """publish 경로 — _latest.json 갱신만 허용된다."""
    global _publish_allowed
    prev = _publish_allowed
    _publish_allowed = True
    try:
        yield
    finally:
        _publish_allowed = prev


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def assert_writable(target: Path) -> None:
    """출력 위치 규칙(design.md §3.1)을 확인한다.

    - data/ · reference/ : 코드가 쓰지 않는다 (기준 입력 묶음 신규 생성도 사람의 몫).
    - artifacts/ : 활성 build_context의 vdir 내부, 또는 publish_context의
      _latest.json 갱신만 허용한다. 그 밖은 새 실행으로 남긴다.
    - 그 외(runs/, docs/internal/investigations/ 등)는 허용.
    """
    t = Path(target).resolve()
    if _is_under(t, DATA.resolve()):
        raise WriteViolation(f"data/는 코드가 쓰지 않는다 (읽기 전용 원시층): {target}")
    if _is_under(t, REFERENCE.resolve()):
        raise WriteViolation(f"reference/는 코드가 쓰지 않는다 (기준 입력 묶음·수동 분류표): {target}")
    if _is_under(t, ARTIFACTS.resolve()):
        if _publish_allowed and t.name == "_latest.json":
            return
        if _active_build_dir is not None and _is_under(t, _active_build_dir):
            return
        raise WriteViolation(
            f"artifacts/ 쓰기는 스테이지 러너의 빌드 구간에서만 허용된다: {target}")
