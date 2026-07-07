"""manifest 읽기/쓰기, sha256 해시, 버전 해석, 재사용 기준값, code_ref (design.md §4.2).

manifest는 "어느 입력·params·코드가 이 산출물을 만들었나"의 유일한 기록이다.
하류는 상류의 _latest를 읽되, 해석된 실제 버전·해시를 자기 manifest에 고정 기록한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bts.paths as paths

KST = timezone(timedelta(hours=9))  # 산출물 타임스탬프 규약 (+09:00)

# 기준 입력 묶음 — 전 스테이지 공통 입력 (verification.md §2: 0번 체크·DIFF 판정의 입력)
_FROZEN_LAYER_FILES = [
    "reference/prior_baseline/raw_hashes.yaml",
    "reference/prior_baseline/baseline.yaml",
    "reference/prior_baseline/known_deviations.yaml",
]


# ── 해시 ────────────────────────────────────────────────────────────────────
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_dir(d: Path) -> str:
    """디렉터리 트리 해시 — 정렬된 (상대경로:파일해시) 목록의 sha256.

    다건 입력(evidence/ 184 JSON 등)을 manifest·content_key에 단일 항목으로 고정한다.
    파일 추가/삭제/개명/내용 변경 전부가 해시를 바꾼다.
    """
    entries = []
    for p in sorted(Path(d).rglob("*")):
        if p.is_file():
            entries.append(f"{p.relative_to(d).as_posix()}:{sha256_file(p)}")
    return sha256_text("\n".join(entries))


def params_hash(params: dict) -> str:
    """params의 정준 직렬화 해시 — manifest.params_hash."""
    return "sha256:" + sha256_text(json.dumps(params, sort_keys=True, ensure_ascii=False, default=str))


# ── code_ref ────────────────────────────────────────────────────────────────
def _src_tree_hash() -> str:
    """src/ 아래 추적 대상 파일(.py/.yaml/.csv)의 결정적 트리 해시."""
    entries = []
    src = paths.ROOT / "src"
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.suffix in {".py", ".yaml", ".yml", ".csv"} \
                and "__pycache__" not in p.parts:
            entries.append(f"{p.relative_to(src).as_posix()}:{sha256_file(p)}")
    return sha256_text("\n".join(entries))


def code_ref() -> str:
    """git sha. 커밋 부재·더러운 트리면 'dirty+' + src 트리 해시 12자."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=paths.ROOT,
            capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=paths.ROOT,
            capture_output=True, text=True, check=True).stdout.strip()
        if not dirty:
            return f"git:{sha}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return "dirty+" + _src_tree_hash()[:12]


# ── 입력 해석 ────────────────────────────────────────────────────────────────
@dataclass
class ResolvedInputs:
    """registry 선언 입력의 해석 결과 — 선언된 입력만 쓰기 위한 매개체.

    artifacts: {상류 stage: {"version": vNNN, "files": {파일명: sha256}}}
    files:     {루트 상대 경로: sha256}  (params.yaml + 스테이지 선언 config)
    """
    artifacts: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)

    def manifest_entries(self) -> list:
        out = []
        for stage, info in sorted(self.artifacts.items()):
            out.append({"artifact": stage, "version": info["version"],
                        "files": info["files"]})
        for f, h in sorted(self.files.items()):
            out.append({"file": f, "sha256": h})
        return out


def read_manifest(vdir: Path) -> dict:
    with open(vdir / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


def verify_outputs(vdir: Path, manifest: dict | None = None) -> list[str]:
    """게시본 출력 파일 sha256 대조 — 불일치 파일 목록 반환(빈 목록 = 일치)."""
    m = manifest or read_manifest(vdir)
    bad = []
    for fname, meta in m.get("outputs", {}).items():
        p = vdir / fname
        if not p.exists() or "sha256:" + sha256_file(p) != meta["sha256"]:
            bad.append(fname)
    return bad


def resolve_inputs(stage: str, scope: str, pins: dict[str, str] | None = None) -> ResolvedInputs:
    """registry 선언 입력만 해석(화이트리스트 — 선언 밖 artifact 접근 경로를 제공하지 않는다).

    상류 _latest 해석 + 파일 sha256 고정. 상류 부재 → UpstreamMissing,
    출력 해시 불일치 → UpstreamCorrupt (둘 다 exit 5 사유).
    """
    from bts.run import REGISTRY  # 지연 import (순환 회피)
    pins = pins or {}
    st = REGISTRY[stage]
    ri = ResolvedInputs()
    for up in st.inputs:
        version = pins.get(up) or paths.latest_version(up, scope)
        vdir = paths.artifact_dir(up, scope, version)
        m = read_manifest(vdir)
        bad = verify_outputs(vdir, m)
        if bad:
            raise paths.UpstreamCorrupt(
                f"상류 출력 해시 불일치: {up}/{scope}/{version} 파일 {bad}")
        ri.artifacts[f"{up}/{scope}"] = {
            "version": version,
            "files": {k: v["sha256"] for k, v in m.get("outputs", {}).items()},
        }
    # config 입력: params.yaml은 전 스테이지 공통 + 스테이지 선언 config 파일.
    # 기준 입력 묶음 3파일도 전 스테이지 공통 입력이다 — DIFF 판정(0번 체크·baseline·known_deviations)의
    # 입력이 content_key에 없으면 등재 변경이 동일 입력 재사용에 가려져 '재이탈 자동 강등'이
    # 무관한 재빌드 없이는 발동하지 않는다 (design.md §4.3-5: '입력 해시 전부').
    cfg_files = (["src/bts/config/params.yaml"]
                 + _FROZEN_LAYER_FILES
                 + [c.format(scope=scope) for c in st.config_files])
    for rel in cfg_files:
        p = paths.ROOT / rel
        if p.exists():
            ri.files[rel] = sha256_file(p)
    # 스테이지 선언 raw/수동 분류 입력(파일 또는 디렉터리) — design.md §4.2 예시 manifest의
    # file 입력 항목. raw는 s00의 '입력 전부'이므로 여기 고정되지 않으면 raw 변경 시
    # 기존 게시본 재사용이 빌드·체크(0번 체크 포함)를 통째로 우회한다.
    for rel in st.input_files.get(scope, []):
        p = paths.ROOT / rel
        if p.is_dir():
            ri.files[rel] = sha256_dir(p)
        elif p.exists():
            ri.files[rel] = sha256_file(p)
        else:
            raise paths.UpstreamMissing(f"registry 선언 입력 파일 부재: {rel}")
    return ri


# ── 재사용 기준값 ───────────────────────────────────────────────────────────
def content_key(inputs: ResolvedInputs, phash: str, code: str) -> str:
    """(입력 해시 전부 + params_hash + code_ref) — 동일 입력 재사용 기준값.

    design.md §4.3-5 문언 그대로 '해시 전부'만 들어간다 — 상류 버전 라벨(vNNN)은
    기준값의 입력이 아니다(내용 동일 = 기준값 동일). 버전 라벨이 기준값에 들어가면 동일 내용의
    상류 재게시만으로도 하류 기준값이 달라져 기존 게시본을 재사용하기 어렵다.
    해석된 실제 버전 라벨은 manifest.inputs에 기록된다(재현 경로 — §4.3-4).
    """
    artifact_hashes = {stage: info["files"]
                       for stage, info in inputs.artifacts.items()}
    payload = json.dumps(
        {"artifacts": artifact_hashes, "files": inputs.files,
         "params_hash": phash, "code_ref": code},
        sort_keys=True, ensure_ascii=False)
    return "sha256:" + sha256_text(payload)


def find_published(stage: str, scope: str, key: str) -> str | None:
    """동일 content_key의 기존 게시본 탐색 — 있으면 그 버전을 재사용한다."""
    sdir = paths.ARTIFACTS / stage / scope
    if not sdir.exists():
        return None
    for d in sorted(sdir.iterdir()):
        if not d.is_dir() or not (d / "manifest.json").exists():
            continue
        m = read_manifest(d)
        if m.get("content_key") == key and m.get("status") == "promoted":
            return m["version"]
    return None


# ── manifest 기록·게시 ───────────────────────────────────────────────────────
def write_manifest(vdir: Path, stage: str, scope: str, inputs: ResolvedInputs,
                   params: dict, outputs: dict, checks_summary: dict,
                   status: str, key: str, code: str,
                   acks=(), reviewed_by=None, caveats=(),
                   upstream_unexplained=(), needs_review: bool = False) -> dict:
    """design.md §4.2 스키마. outputs = {파일명: {"rows": n|None, "sha256": ...}}.

    needs_review: 빌드 시점의 review 게이트 술어 고정 기록(design.md §4.3-3 —
    'override 1행 이상 실린 상태로 빌드된' 여부). promote가 현재 디스크 파일 대신
    이 기록으로 판정한다 (TOCTOU 차단 — 검증 라운드 1 Stage 2 수리).
    """
    m = {
        "stage": stage, "scope": scope, "version": vdir.name,
        "created_at": datetime.now(KST).isoformat(timespec="seconds"),
        "code_ref": code,
        "params": params,
        "params_hash": params_hash(params),
        "content_key": key,
        "inputs": inputs.manifest_entries(),
        "outputs": outputs,
        "checks_summary": checks_summary,
        "acks": list(acks),
        "reviewed_by": reviewed_by,
        "needs_review": bool(needs_review),
        "caveats": list(caveats),
        "upstream_unexplained": list(upstream_unexplained),
        "status": status,
    }
    p = vdir / "manifest.json"
    paths.assert_writable(p)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    return m


def _output_rows(p: Path) -> int | None:
    """산출물 행수 (design.md §4.2 스키마의 rows — 자가 문서화). 표 포맷이 아니면 None."""
    if p.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
            return int(pq.ParquetFile(p).metadata.num_rows)
        except ImportError:
            import pandas as pd
            return int(len(pd.read_parquet(p)))
    if p.suffix == ".csv":
        with open(p, encoding="utf-8-sig") as f:
            return max(sum(1 for _ in f) - 1, 0)   # 헤더 제외
    return None


def hash_outputs(vdir: Path) -> dict:
    """vdir 내 산출물 파일의 {파일명: {rows, sha256}} 집계 (manifest용)."""
    out = {}
    for p in sorted(vdir.iterdir()):
        if p.is_file() and p.name not in {"manifest.json", "checks.json"}:
            out[p.name] = {"rows": _output_rows(p),
                           "sha256": "sha256:" + sha256_file(p)}
    return out


def publish(stage: str, scope: str, vdir: Path) -> None:
    """_latest.json 원자 갱신 (promote 경로만)."""
    latest = paths.ARTIFACTS / stage / scope / "_latest.json"
    with paths.publish_context():
        paths.assert_writable(latest)
        tmp = latest.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": vdir.name,
                       "promoted_at": datetime.now(KST).isoformat(timespec="seconds")},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, latest)
