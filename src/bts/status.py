"""bts status — 게시 현황 + STALE 전파 + UNEXPLAINED 배지 (design.md §4.3-7, §6.2).

STALE: 입력 해시가 어긋난 게시본 — 상류 artifact 재게시뿐 아니라 file 입력
(raw·params·rules·overrides·기준 입력 묶음) 드리프트도 포함한다(검증 라운드 2 수리).
부분 재실행 누락이 명령 한 번으로 드러난다. 판정 기준은 design.md §4.3-7 문언
그대로 '해시'다 — 버전 라벨만 다르고 내용 해시가 동일하면 STALE이 아니다
(content_key와 동일 원리: 내용 동일 = 재실행 불요).
"""
from __future__ import annotations

import json
import sys

import bts.paths as paths
import bts.manifest as manifest


def _current_upstream(entry: dict, scope: str) -> tuple[str, dict] | None:
    """artifact 입력 항목의 '현재' 상류 게시본 (version, outputs 해시)."""
    up_stage = entry["artifact"].split("/")[0]
    try:
        version = paths.latest_version(up_stage, scope)
        m = manifest.read_manifest(paths.artifact_dir(up_stage, scope, version))
    except (paths.PathError, FileNotFoundError):
        return None
    return version, {k: v["sha256"] for k, v in m.get("outputs", {}).items()}


def _file_input_change_reason(entry: dict) -> str | None:
    """file 입력 항목(파일 또는 디렉터리)의 현재 해시 대조 — 드리프트 사유 반환."""
    rel = entry["file"]
    p = paths.ROOT / rel
    if p.is_dir():
        cur = manifest.sha256_dir(p)
    elif p.exists():
        cur = manifest.sha256_file(p)
    else:
        return f"{rel}: 입력 파일 소실"
    if cur != entry.get("sha256"):
        return f"{rel}: 해시 드리프트"
    return None


def stage_status(stage: str, scope: str) -> dict | None:
    """단일 스테이지/스코프의 상태 사전. 게시본 없으면 None."""
    try:
        version = paths.latest_version(stage, scope)
        m = manifest.read_manifest(paths.artifact_dir(stage, scope, version))
    except (paths.PathError, FileNotFoundError):
        return None
    stale_because = []
    for e in m.get("inputs", []):
        if "file" in e:
            why = _file_input_change_reason(e)
            if why:
                stale_because.append(why)
            continue
        if "artifact" not in e:
            continue
        cur = _current_upstream(e, scope)
        if cur is None:
            stale_because.append(f"{e['artifact']}: 상류 게시본 소실")
            continue
        cur_version, cur_files = cur
        if cur_files != e.get("files", {}):
            stale_because.append(f"{e['artifact']}: {e['version']} → {cur_version}"
                                 if cur_version != e["version"]
                                 else f"{e['artifact']}: {e['version']} 출력 해시 드리프트")
    baseline_summary = m.get("checks_summary", {}).get("DIFF", {})
    has_unexplained_baseline_diff = (
        isinstance(baseline_summary, dict) and baseline_summary.get("unexplained", 0) > 0
    ) \
        or bool(m.get("upstream_unexplained"))
    return {
        "stage": stage, "scope": scope, "version": version,
        "status": "STALE" if stale_because else "OK",
        "stale_because": stale_because,
        "unexplained_badge": has_unexplained_baseline_diff,
        "upstream_unexplained": m.get("upstream_unexplained", []),
        "reviewed_by": m.get("reviewed_by"),
    }


def collect() -> list[dict]:
    from bts.run import REGISTRY
    rows = []
    for stage, st in REGISTRY.items():
        for scope in st.scopes:
            s = stage_status(stage, scope)
            rows.append(s or {"stage": stage, "scope": scope, "version": "-",
                              "status": "UNBUILT", "stale_because": [],
                              "unexplained_badge": False,
                              "upstream_unexplained": [], "reviewed_by": None})
    return rows


def main(argv=None) -> int:
    from bts.run import _utf8_console
    _utf8_console()
    rows = collect()
    print(f"{'stage':<16} {'scope':<7} {'ver':<6} {'status':<8} badge")
    print("-" * 60)
    for r in rows:
        badge = "UNEXPLAINED" if r["unexplained_badge"] else ""
        print(f"{r['stage']:<16} {r['scope']:<7} {r['version']:<6} {r['status']:<8} {badge}")
        for why in r["stale_because"]:
            print(f"{'':<31} └ {why}")
    if "--json" in (argv or sys.argv[1:]):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
