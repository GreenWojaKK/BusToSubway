"""스테이지 러너 — python -m bts.run <stage> --scope <s> [--pin ...] (design.md §4.3, §6).

실행 = 빌드 → 체크 → 게이트 판정 → 게시/거부/보류가 원자적이다.
"검증 없는 산출물"이 생길 수 있는 경로는 없다(verification.md).

registry가 배선의 검증 기준이다: 스테이지 간 결합은 여기 선언된 입력뿐이며,
러너가 입력 화이트리스트를 강제한다(선언 밖 artifact 읽기는 경로 헬퍼가 거부).
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime

import bts.paths as paths
import bts.manifest as manifest
from bts.checks import core
from bts.io import ContractViolation

# exit code 규약 (verification.md §3.2)
EXIT_OK = 0                 # 전부 통과 (DIFF는 MATCH/EXPLAINED만) — 게시
EXIT_CONTRACT = 2           # CONTRACT FAIL — 거부, vNNN-rejected/ 보존
EXIT_PHYSICAL = 3           # PHYSICAL WARN 미승인 — 보류
EXIT_UNEXPLAINED = 4        # 게시됐으나 신규 UNEXPLAINED DIFF — 조사 메모 생성
EXIT_UPSTREAM = 5           # 상류 오염 (해시 불일치·_latest 부재) — 실행 거부


@dataclass
class Stage:
    """registry 항목 — 배선의 검증 기준 (design.md 원칙 3).

    builders/checks 값: callable 또는 dotted 참조 문자열("pkg.mod:func" / "pkg.mod").
    config_files의 "{scope}"는 실행 스코프로 치환된다.
    input_files: scope -> raw/판단층 입력(파일 또는 디렉터리, 루트 상대) — manifest inputs와
    content_key에 해시 고정된다(design.md §4.3-5 '입력 해시 전부': raw 변경이 멱등 스킵을
    우회하지 못하게 하는 핀).
    review_overrides: 이 override 파일에 데이터 행이 1행 이상 실린 채 빌드되면
    게시에 --reviewed-by가 필요하다 (design.md §4.3-3).
    """
    builders: dict = field(default_factory=dict)     # scope -> build(inputs, params, vdir)
    inputs: list = field(default_factory=list)       # ★ 입력 화이트리스트
    checks: dict = field(default_factory=dict)       # scope -> run(ctx) -> list[CheckResult]
    config_files: list = field(default_factory=list)
    input_files: dict = field(default_factory=dict)  # scope -> [raw 파일/디렉터리]
    review_overrides: list = field(default_factory=list)
    scopes: tuple = ("before", "after")


REGISTRY: dict[str, Stage] = {
    "s00_ingest": Stage(
        builders={"before": "bts.stages.s00_ingest.before:build",
                  "after": "bts.stages.s00_ingest.after:build"},
        inputs=[],                                   # 상류 artifact 없음 (raw는 input_files 핀)
        checks={"before": "bts.checks.contracts.s00_before",
                "after": "bts.checks.contracts.s00_after"},
        config_files=["src/bts/config/overrides/name_aliases.csv"],
        input_files={                                # raw = s00의 입력 전부 (해시 핀 — §4.3-5)
            "before": ["data/ulsan_route_schedule_before.parquet",
                       "data/ulsan_stops_before.parquet",
                       "data/ulsan_bus_route_before.parquet",
                       "reference/variant_tagging/variant_tags.csv",
                       "reference/variant_tagging/evidence"],
            "after": ["data/ulsan_route_schedule_after.parquet",
                      "data/ulsan_stops_after.parquet",
                      "data/ulsan_bus_route_after.parquet"]}),
    "s01_canonical": Stage(
        builders={"before": "bts.stages.s01_canonical.before:build",
                  "after": "bts.stages.s01_canonical.after:build"},
        inputs=["s00_ingest"],                       # ★ 입력 화이트리스트
        checks={"before": "bts.checks.contracts.s01_before",
                "after": "bts.checks.contracts.s01_after"},
        config_files=["src/bts/config/route_class_rules.{scope}.yaml"],
        input_files={                                # 판단층 직접 읽기 입력 (design.md §4.2 예시)
            "before": ["reference/variant_tagging/variant_tags.csv",
                       "reference/variant_tagging/evidence"]}),
    "s02_place": Stage(
        builders={"before": "bts.stages.s02_place.merge:build_before",
                  "after": "bts.stages.s02_place.merge:build_after"},
        inputs=["s00_ingest"],                       # stops.parquet만 소비 — s01과 독립(병렬 가능)
        checks={"before": "bts.checks.contracts.s02_before",
                "after": "bts.checks.contracts.s02_after"},
        config_files=["src/bts/config/overrides/place_overrides.{scope}.csv",
                      "src/bts/config/overrides/name_aliases.csv"],
        # raw 직접 읽기 없음 → input_files 미선언 (stops는 s00 artifact — spec §3)
        review_overrides=["src/bts/config/overrides/place_overrides.{scope}.csv"]),
    "s03_hub": Stage(
        builders={"before": "bts.stages.s03_hub.metrics:build_before"},
        inputs=["s01_canonical", "s02_place"],   # backbone·catalog·(주석용)patterns + 맵/places
        checks={"before": "bts.checks.contracts.s03_before"},
        config_files=["src/bts/config/overrides/hub_overrides.csv"],
        review_overrides=["src/bts/config/overrides/hub_overrides.csv"],
        scopes=("before",)),     # after는 기준값 부재 + 편도 분리(ADR-002) 미해결로 후속 (spec §0)
}


def _resolve_ref(ref):
    """callable 또는 'module:func' dotted 참조 해석."""
    if callable(ref):
        return ref
    mod_name, _, fn = str(ref).partition(":")
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as e:
        raise NotImplementedError(
            f"스테이지 구현 모듈 부재: {mod_name} — registry 골격만 존재한다") from e
    return getattr(mod, fn) if fn else mod


def stage_params(stage: str) -> dict:
    return paths.load_params().get("stages", {}).get(stage, {})


def _ack_entry(check_id: str, reviewed_by: str) -> dict:
    """manifest acks 항목 — 주체·시각 기록 (design.md §4.2: {check_id, by, at}).

    익명 ack은 존재하지 않는다 — ack 적용 경로(run/promote)가 --reviewed-by 부재 시
    ack을 거부하므로, 이 함수는 항상 식별된 주체와 함께 호출된다 (검증 라운드 2 수리).
    """
    return {"check_id": check_id, "by": reviewed_by,
            "at": datetime.now(manifest.KST).isoformat(timespec="seconds")}


def _needs_review(st: Stage, scope: str) -> bool:
    """review_overrides 파일에 데이터 행(헤더 제외)이 있으면 True — '현재 디스크' 판독.

    빌드 경로(run) 전용: 빌드와 같은 프로세스에서 호출되어 판독 = 빌드 시점 상태다.
    게시 경로(promote)는 이 함수를 직접 쓰면 안 된다 — 빌드~promote 사이 파일 원복으로
    게이트가 우회되는 TOCTOU가 생긴다. promote는 _version_needs_review(버전 자신의 기록)로
    판정한다 (design.md §4.3-3의 술어는 '1행 이상 실린 상태로 **빌드된**' — 검증 라운드 1 수리).
    """
    for rel in st.review_overrides:
        p = paths.ROOT / rel.format(scope=scope)
        if p.exists():
            lines = [ln for ln in p.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
            if len(lines) > 1:
                return True
    return False


def _version_needs_review(m: dict, st: Stage, scope: str) -> bool:
    """게시 대상 버전의 '빌드 시점' review 필요 여부 (TOCTOU 수리 — 검증 라운드 1).

    ① manifest.needs_review — 러너가 빌드 시점에 고정 기록한 값(수리 이후 버전) 우선.
    ② 구버전 manifest(필드 부재) 폴백: manifest inputs에 핀된 override 파일 해시가
       현재 디스크 파일과 일치할 때만 현재 파일 판독을 빌드 시점의 대리 증거로 인정.
       불일치·핀 부재는 빌드 시점 상태를 복원할 수 없으므로 보수적으로 True
       (판정 불능 상태의 무심사 게시 금지 — 재실행이 정답).
    """
    if "needs_review" in m:
        return bool(m["needs_review"])
    if not st.review_overrides:
        return False
    pinned = {e["file"]: e["sha256"] for e in m.get("inputs", []) if "file" in e}
    for rel in st.review_overrides:
        rel_s = rel.format(scope=scope)
        p = paths.ROOT / rel_s
        if rel_s not in pinned or not p.exists() \
                or manifest.sha256_file(p) != pinned[rel_s]:
            return True
    return _needs_review(st, scope)


def _upstream_unexplained(inputs: manifest.ResolvedInputs, scope: str) -> list[str]:
    """상류 게시본의 UNEXPLAINED를 하류 manifest로 전파 (design.md §6.2)."""
    out = []
    for key, info in inputs.artifacts.items():
        up_stage = key.split("/")[0]
        try:
            m = manifest.read_manifest(paths.artifact_dir(up_stage, scope, info["version"]))
        except (paths.PathError, FileNotFoundError):
            continue
        baseline_summary = m.get("checks_summary", {}).get("DIFF", {})
        if isinstance(baseline_summary, dict) and baseline_summary.get("unexplained", 0):
            out.append(f"{key}@{info['version']}")
        out.extend(m.get("upstream_unexplained", []))
    return sorted(set(out))


def _record_run(stage: str, scope: str, exit_code: int, version: str | None,
                key: str, note: str = "") -> None:
    """runs/ 실행 기록 — 게시 여부와 무관하게 전 실행 (verification.md §3.3)."""
    paths.RUNS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(manifest.KST).strftime("%Y%m%dT%H%M%S")
    rec = {"timestamp": ts, "stage": stage, "scope": scope,
           "exit_code": exit_code, "version": version,
           "content_key": key, "note": note}
    p = paths.RUNS / f"{ts}_{stage}_{scope}.json"
    n = 2
    while p.exists():
        p = paths.RUNS / f"{ts}_{stage}_{scope}_{n}.json"
        n += 1
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


def run(stage: str, scope: str, pins: dict | None = None, acks: list | None = None,
        reviewed_by: str | None = None) -> int:
    """resolve → 멱등 스킵 → build → checks → 게이트 → 기록 → 게시/거부/보류."""
    pins, acks = pins or {}, list(acks or [])
    if stage not in REGISTRY:
        print(f"[bts.run] 미등록 스테이지: {stage} (registry: {sorted(REGISTRY)})")
        return EXIT_UPSTREAM
    st = REGISTRY[stage]
    if scope not in st.scopes:
        print(f"[bts.run] {stage}에 없는 scope: {scope}")
        return EXIT_UPSTREAM

    # 1) 입력 해석 (화이트리스트 + 상류 무결성)
    try:
        inputs = manifest.resolve_inputs(stage, scope, pins)
    except (paths.UpstreamMissing, paths.UpstreamCorrupt) as e:
        print(f"[bts.run] 상류 오염/부재 (exit {EXIT_UPSTREAM}): {e}")
        _record_run(stage, scope, EXIT_UPSTREAM, None, "", str(e))
        return EXIT_UPSTREAM

    params = stage_params(stage)
    phash = manifest.params_hash(params)
    code = manifest.code_ref()
    key = manifest.content_key(inputs, phash, code)

    # 2) 멱등 스킵 (content-addressed 캐시 — design.md §4.3-5)
    existing = manifest.find_promoted(stage, scope, key)
    if existing:
        note = "idempotent_skip"
        # flip-flop 해소 (검증 라운드 2 수리): 기준 입력 묶음·params 원복 등으로 재사용 대상이
        # _latest가 아닌 구버전일 수 있다 — 재사용 = 그 버전이 현재 입력의 정본이므로
        # _latest를 재사용 버전으로 동기화한다(하류가 현 입력과 다른 내용을 읽는 것 방지).
        try:
            current = paths.latest_version(stage, scope)
        except paths.UpstreamMissing:
            current = None
        if current != existing:
            manifest.promote(stage, scope, paths.artifact_dir(stage, scope, existing))
            note = f"idempotent_skip_latest_synced:{current}->{existing}"
            print(f"[bts.run] 멱등 재사용 대상({existing})이 _latest({current})와 달라 "
                  f"_latest를 동기화했다")
        print(f"[bts.run] 멱등 스킵: {stage}/{scope} — 동일 content_key 게시본 {existing} 재사용")
        _record_run(stage, scope, EXIT_OK, existing, key, note)
        return EXIT_OK

    # 3) 빌드 + 체크 (원자적 — build_context 안에서만 artifacts 쓰기 허용)
    builder = _resolve_ref(st.builders[scope])   # 미구현이면 vdir 생성 전에 실패
    # review 게이트 술어는 빌드 시점에 1회 판독해 manifest에 고정 기록한다 — promote가
    # 나중에 현재 파일을 다시 읽으면 원복/주입으로 판정이 바뀐다 (TOCTOU, 검증 라운드 1 수리)
    needs_review = _needs_review(st, scope)
    vdir = paths.new_version_dir(stage, scope)
    try:
        with paths.build_context(vdir):
            builder(inputs, params, vdir)
            results = core.run_stage_checks(stage, scope, vdir, params, inputs,
                                            checks_ref=st.checks.get(scope))
            core.write_checks_json(vdir, results)
            summary = core.summarize(results)

            blocking_failures = [
                r for r in results if r.check_class == "CONTRACT" and r.status == "FAIL"
            ]
            sanity_failures = [
                r for r in results if r.check_class == "PHYSICAL" and r.status == "FAIL"
            ]
            unapproved_sanity_failures = [r for r in sanity_failures if r.check_id not in acks]
            unexplained_baseline_diffs = [
                r for r in results if r.check_class == "DIFF" and r.status == "UNEXPLAINED"
            ]
            approved_sanity_ids = [r.check_id for r in sanity_failures if r.check_id in acks]
            if approved_sanity_ids and not reviewed_by:
                # 익명 ack 거부 (검증 라운드 2 수리): ack은 판정 이력이다 — 주체 식별
                # (--reviewed-by) 없는 ack은 적용하지 않고 보류한다.
                print(f"[bts.run] 익명 ack 거부: --ack {approved_sanity_ids}는 --reviewed-by <이름>과 "
                      f"함께만 적용된다 — 보류")
                unapproved_sanity_failures, approved_sanity_ids = sanity_failures, []
            if approved_sanity_ids:
                summary["PHYSICAL"]["acked"] = len(approved_sanity_ids)

            review_block = needs_review and not reviewed_by

            if blocking_failures:
                status = "rejected"
            elif unapproved_sanity_failures or review_block:
                status = "pending"
            else:
                status = "promoted"

            upu = _upstream_unexplained(inputs, scope)
            manifest.write_manifest(
                vdir, stage, scope, inputs, params,
                outputs=manifest.hash_outputs(vdir),
                checks_summary=summary, status=status, key=key, code=code,
                acks=[_ack_entry(c, reviewed_by) for c in approved_sanity_ids],
                reviewed_by=reviewed_by, upstream_unexplained=upu,
                needs_review=needs_review)
    except ContractViolation as e:
        # 빌드 중 검증 규칙 위반(로더 등) — CONTRACT FAIL과 같은 의미론: exit 2 + 포렌식 보존
        # (traceback exit 1로 새면 runs/ 기록도 rejected 개명도 없이 고아 vNNN이 남는다)
        rej = paths.mark_rejected(vdir)
        print(f"[bts.run] 빌드 중 ContractViolation → 거부 (exit {EXIT_CONTRACT}) — "
              f"보존: {rej.name}\n  {e}")
        _record_run(stage, scope, EXIT_CONTRACT, rej.name, key, f"build_contract_violation:{e}")
        return EXIT_CONTRACT
    except BaseException as e:
        # 예기치 못한 크래시 — 고아 버전 디렉터리를 남기지 않고 runs/에 흔적을 남긴 뒤 재던짐
        rej = paths.mark_rejected(vdir)
        _record_run(stage, scope, 1, rej.name, key,
                    f"build_crash:{type(e).__name__}:{e}")
        raise

    # 4) 게이트 판정 → exit code (verification.md §3.2)
    if blocking_failures:
        rej = paths.mark_rejected(vdir)
        ids = [r.check_id for r in blocking_failures]
        print(f"[bts.run] CONTRACT FAIL {ids} → 거부 (exit {EXIT_CONTRACT}) — 보존: {rej.name}")
        _record_run(stage, scope, EXIT_CONTRACT, rej.name, key, f"blocking_validation_failed:{ids}")
        return EXIT_CONTRACT

    if unapproved_sanity_failures:
        ids = [r.check_id for r in unapproved_sanity_failures]
        print(f"[bts.run] PHYSICAL 미승인 {ids} → 보류 (exit {EXIT_PHYSICAL}). "
              f"재개: python -m bts.run promote {stage} {scope} {vdir.name} --ack <id>")
        _record_run(stage, scope, EXIT_PHYSICAL, vdir.name, key, f"sanity_check_unapproved:{ids}")
        return EXIT_PHYSICAL

    if review_block:
        print(f"[bts.run] 판단 개입 산출물(override 적용) — --reviewed-by 필요, 보류 (exit {EXIT_PHYSICAL})")
        _record_run(stage, scope, EXIT_PHYSICAL, vdir.name, key, "needs_reviewed_by")
        return EXIT_PHYSICAL

    manifest.promote(stage, scope, vdir)
    if unexplained_baseline_diffs:
        ids = [r.check_id for r in unexplained_baseline_diffs]
        print(f"[bts.run] 게시 + 신규 DIFF UNEXPLAINED {ids} (exit {EXIT_UNEXPLAINED}) — 조사 메모 생성됨")
        _record_run(stage, scope, EXIT_UNEXPLAINED, vdir.name, key, f"unexplained:{ids}")
        return EXIT_UNEXPLAINED

    print(f"[bts.run] {stage}/{scope} {vdir.name} 게시 (exit {EXIT_OK})")
    _record_run(stage, scope, EXIT_OK, vdir.name, key, "promoted")
    return EXIT_OK


def promote_pending(stage: str, scope: str, version: str, acks: list,
                    reviewed_by: str | None) -> int:
    """보류(pending) 버전의 게시 재개 — checks.json 재판독 + --ack 적용.

    전 경로가 runs/에 기록된다(verification.md §3.3 — 게시 여부와 무관하게 전 실행).
    익명 ack 금지: --ack은 --reviewed-by(주체 식별) 없이는 거부된다 (검증 라운드 2 수리).
    검증 라운드 1(Stage 2) 수리 4건:
    - review 게이트는 현재 디스크의 override 파일이 아니라 버전 자신의 빌드 시점 기록
      (_version_needs_review)으로 판정한다 — 파일 원복에 의한 TOCTOU 우회 차단.
    - 게시 직전 출력 해시 재검증(verify_outputs) — 빌드~promote 사이 변조는 exit 5 거부.
    - ack 기록은 run 경로와 동일하게 PHYSICAL FAIL 교집합만 — 미지/PASS 체크의 ack이
      판정 이력으로 오염되지 않는다.
    - 게시 버전의 checks.json에 UNEXPLAINED DIFF가 있으면 run 경로와 동일하게 exit 4
      (게시 자체는 비차단 — 신호가 exit code 채널에서도 유실되지 않게).
    """
    vdir = paths.artifact_dir(stage, scope, version)
    m = manifest.read_manifest(vdir)
    key = m.get("content_key", "")
    if acks and not reviewed_by:
        print(f"[bts.run] 익명 ack 거부: --ack {acks}는 --reviewed-by <이름>과 함께만 "
              f"적용된다 (exit {EXIT_PHYSICAL})")
        _record_run(stage, scope, EXIT_PHYSICAL, version, key,
                    f"promote_anonymous_ack_refused:{acks}")
        return EXIT_PHYSICAL
    if m.get("status") == "promoted":
        print(f"[bts.run] 이미 게시됨: {stage}/{scope}/{version}")
        _record_run(stage, scope, EXIT_OK, version, key, "promote_already_promoted")
        return EXIT_OK
    if m.get("status") == "rejected":
        print(f"[bts.run] rejected 버전은 게시 불가 — 재실행하라")
        _record_run(stage, scope, EXIT_CONTRACT, version, key, "promote_rejected_version")
        return EXIT_CONTRACT
    bad = manifest.verify_outputs(vdir, m)
    if bad:
        print(f"[bts.run] 산출물 해시 불일치(빌드 후 변조) {bad} — 게시 거부 "
              f"(exit {EXIT_UPSTREAM}), 재실행하라")
        _record_run(stage, scope, EXIT_UPSTREAM, version, key,
                    f"promote_outputs_corrupt:{bad}")
        return EXIT_UPSTREAM
    with open(vdir / "checks.json", encoding="utf-8") as f:
        results = json.load(f)
    sanity_failures = [
        r for r in results if r["check_class"] == "PHYSICAL" and r["status"] == "FAIL"
    ]
    unapproved_sanity_ids = [r["check_id"] for r in sanity_failures if r["check_id"] not in acks]
    if unapproved_sanity_ids:
        print(f"[bts.run] PHYSICAL 미승인 잔존 {unapproved_sanity_ids} (exit {EXIT_PHYSICAL})")
        _record_run(stage, scope, EXIT_PHYSICAL, version, key,
                    f"promote_sanity_check_unapproved:{unapproved_sanity_ids}")
        return EXIT_PHYSICAL
    if _version_needs_review(m, REGISTRY[stage], scope) and not reviewed_by:
        print(f"[bts.run] 판단 개입 상태로 빌드된 버전 — --reviewed-by 필요 (exit {EXIT_PHYSICAL})")
        _record_run(stage, scope, EXIT_PHYSICAL, version, key, "promote_needs_reviewed_by")
        return EXIT_PHYSICAL
    # ack은 판정 이력 — 이 버전의 PHYSICAL FAIL에 대응하는 것만 기록 (run 경로와 대칭)
    sanity_failure_ids = {r["check_id"] for r in sanity_failures}
    approved_sanity_ids = [c for c in acks if c in sanity_failure_ids]
    ignored = sorted(set(acks) - sanity_failure_ids)
    if ignored:
        print(f"[bts.run] --ack 중 이 버전의 PHYSICAL FAIL이 아닌 항목은 기록하지 않는다: {ignored}")
    # pending 버전은 아직 불변 대상이 아니다 — manifest에 승인 기록 후 게시
    m["acks"] = [_ack_entry(c, reviewed_by) for c in approved_sanity_ids]
    m["reviewed_by"] = reviewed_by
    m["status"] = "promoted"
    if isinstance(m["checks_summary"].get("PHYSICAL"), dict):
        m["checks_summary"]["PHYSICAL"]["acked"] = len(m["acks"])
    with paths.build_context(vdir):
        with open(vdir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    manifest.promote(stage, scope, vdir)
    unexplained_baseline_diffs = [
        r["check_id"] for r in results
        if r["check_class"] == "DIFF" and r["status"] == "UNEXPLAINED"
    ]
    if unexplained_baseline_diffs:
        print(f"[bts.run] {stage}/{scope}/{version} 게시 + UNEXPLAINED DIFF {unexplained_baseline_diffs} "
              f"(exit {EXIT_UNEXPLAINED}) — 조사 메모는 빌드 시점에 생성됨")
        _record_run(stage, scope, EXIT_UNEXPLAINED, version, key,
                    f"promoted_via_promote_unexplained:acks={approved_sanity_ids}:{unexplained_baseline_diffs}")
        return EXIT_UNEXPLAINED
    print(f"[bts.run] {stage}/{scope}/{version} 게시 (--ack {approved_sanity_ids}, reviewed_by={reviewed_by})")
    _record_run(stage, scope, EXIT_OK, version, key,
                f"promoted_via_promote:acks={approved_sanity_ids}")
    return EXIT_OK


def _topo_order() -> list[str]:
    """registry DAG 순서 (Kahn)."""
    order, seen = [], set()

    def visit(s):
        if s in seen:
            return
        for up in REGISTRY[s].inputs:
            visit(up)
        seen.add(s)
        order.append(s)

    for s in REGISTRY:
        visit(s)
    return order


def run_all(scope: str, pins, acks, reviewed_by) -> int:
    """DAG 순서 전 스테이지 실행 — 멱등 스킵 포함. 차단 exit에서 중단."""
    worst = EXIT_OK
    for s in _topo_order():
        if scope not in REGISTRY[s].scopes:
            continue
        rc = run(s, scope, pins, acks, reviewed_by)
        if rc in (EXIT_CONTRACT, EXIT_PHYSICAL, EXIT_UPSTREAM):
            return rc
        worst = max(worst, rc)
    return worst


def recheck(scope: str) -> int:
    """게시본 전체 재검증 (변조·회귀 감시) — python -m bts.run checks --scope s."""
    worst = EXIT_OK
    for s in _topo_order():
        try:
            vdir = paths.artifact_dir(s, scope)
        except paths.UpstreamMissing:
            continue
        m = manifest.read_manifest(vdir)
        bad = manifest.verify_outputs(vdir, m)
        if bad:
            print(f"[checks] {s}/{scope}/{m['version']}: 해시 불일치(변조) {bad}")
            worst = max(worst, EXIT_UPSTREAM)
            continue
        pins = {e["artifact"].split("/")[0]: e["version"]
                for e in m["inputs"] if "artifact" in e}
        try:
            inputs = manifest.resolve_inputs(s, scope, pins)
        except (paths.UpstreamMissing, paths.UpstreamCorrupt) as e:
            print(f"[checks] {s}/{scope}: 상류 재검증 실패 — {e}")
            worst = max(worst, EXIT_UPSTREAM)
            continue
        results = core.run_stage_checks(s, scope, vdir, m["params"], inputs,
                                        checks_ref=REGISTRY[s].checks.get(scope))
        fails = [r.check_id for r in results if r.failed]
        print(f"[checks] {s}/{scope}/{m['version']}: "
              f"{'PASS' if not fails else 'FAIL ' + str(fails)}")
        if any(r.check_class == "CONTRACT" and r.status == "FAIL" for r in results):
            worst = max(worst, EXIT_CONTRACT)
    return worst


def _utf8_console() -> None:
    """Windows 콘솔(cp949)에서 한국어·기호 출력 보장."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv=None) -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(
        prog="python -m bts.run",
        description="BTS 스테이지 러너 — 빌드→체크→게시 게이트를 원자적으로 실행한다 "
                    "(design.md §4.3, verification.md §3.2).")
    ap.add_argument("stage",
                    help="스테이지명 | all | checks | promote (registry: %s)" % ", ".join(REGISTRY))
    ap.add_argument("rest", nargs="*",
                    help="promote 서브커맨드 전용: <stage> <scope> <version>")
    ap.add_argument("--scope", choices=["before", "after"],
                    help="스코프 파티션 (design.md §7.1)")
    ap.add_argument("--pin", action="append", default=[], metavar="STAGE=vNNN",
                    help="상류 버전 고정 (재현 실행)")
    ap.add_argument("--ack", action="append", default=[], metavar="CHECK_ID",
                    help="PHYSICAL WARN 명시 승인")
    ap.add_argument("--reviewed-by", default=None,
                    help="판단 개입 산출물의 게시 승인자 기록")
    args = ap.parse_args(argv)

    pins = {}
    for p in args.pin:
        k, _, v = p.partition("=")
        pins[k] = v

    if args.stage == "promote":
        if len(args.rest) != 3:
            ap.error("promote는 <stage> <scope> <version> 3인자가 필요하다")
        return promote_pending(args.rest[0], args.rest[1], args.rest[2],
                               args.ack, args.reviewed_by)
    if args.scope is None:
        ap.error("--scope가 필요하다")
    try:
        if args.stage == "all":
            return run_all(args.scope, pins, args.ack, args.reviewed_by)
        if args.stage == "checks":
            return recheck(args.scope)
        return run(args.stage, args.scope, pins, args.ack, args.reviewed_by)
    except NotImplementedError as e:
        print(f"[bts.run] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
