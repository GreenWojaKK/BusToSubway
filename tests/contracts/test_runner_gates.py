# 러너 게시 게이트·멱등 스킵·exit code의 실동작 검증 (design.md §4.3, verification.md §3.2)
# 더미 스테이지 사용 — 스테이지 구현 없이 러너 골격 자체를 검증한다.
import json

import bts.paths as paths
import bts.manifest as manifest
import bts.run as run
import bts.status as status


def test_전부_통과_게시_exit0(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    vdir = paths.artifact_dir("t90_dummy", "before")
    assert vdir.name == "v001"
    m = manifest.read_manifest(vdir)
    assert m["status"] == "promoted"
    assert (vdir / "checks.json").exists()
    assert m["content_key"].startswith("sha256:")


def test_멱등_스킵_새_버전_미생성(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert run.run("t90_dummy", "before") == run.EXIT_OK   # 동일 입력 재실행
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    versions = [d.name for d in sdir.iterdir() if d.is_dir()]
    assert versions == ["v001"]                             # content-addressed 캐시


def test_params_변경은_새_버전(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    sandbox["state"]["params"]["t90_dummy"] = {"v": 2}
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v002"


def test_CONTRACT_FAIL은_exit2_rejected_보존(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "contract_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_CONTRACT
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    names = [d.name for d in sdir.iterdir() if d.is_dir()]
    assert names == ["v001-rejected"]                       # 포렌식 보존
    assert not (sdir / "_latest.json").exists()             # 게시 안 됨
    m = manifest.read_manifest(sdir / "v001-rejected")
    assert m["status"] == "rejected"


def test_PHYSICAL_미승인은_exit3_보류(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    assert not (sdir / "_latest.json").exists()
    m = manifest.read_manifest(sdir / "v001")
    assert m["status"] == "pending"


def test_PHYSICAL_ack로_게시_재개(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    # bts.run promote <stage> <scope> <version> --ack ... 경로 — 주체 식별 필수
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by="whtnm") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["acks"][0]["check_id"] == "P-DUM-X-001"
    assert m["acks"][0]["by"] == "whtnm"


def test_promote_익명_ack_거부(sandbox):
    # --ack에 --reviewed-by가 없으면 거부 — 익명 ack('user') 이력 제거 (검증 라운드 2)
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by=None) == run.EXIT_PHYSICAL
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending"                         # 게시되지 않음
    assert m["acks"] == []                                  # 익명 ack 기록 없음
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    assert not (sdir / "_latest.json").exists()


def test_PHYSICAL_인라인_ack는_reviewed_by와_함께_즉시_게시(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before",
                   acks=["P-DUM-X-001"], reviewed_by="whtnm") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"


def test_인라인_익명_ack는_적용되지_않고_보류(sandbox):
    # run 경로도 동일 규율 — --ack만으로는 ack이 적용되지 않는다(익명 ack 제거)
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before", acks=["P-DUM-X-001"]) == run.EXIT_PHYSICAL
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending"
    assert m["acks"] == []


def test_신규_UNEXPLAINED는_게시_후_exit4_스텁_생성(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "diff_unexplained"}
    assert run.run("t90_dummy", "before") == run.EXIT_UNEXPLAINED
    assert paths.latest_version("t90_dummy", "before") == "v001"   # 비차단 SIGNAL
    stubs = list(sandbox["stub_dir"].glob("DIFF-*.md"))
    assert len(stubs) == 1                                          # 규명 스텁 강제
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["checks_summary"]["DIFF"]["unexplained"] == 1


def test_상류_부재는_exit5(sandbox):
    assert run.run("t91_downstream", "before") == run.EXIT_UPSTREAM


def test_상류_변조는_exit5(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    vdir = paths.artifact_dir("t90_dummy", "before")
    # 게시본 변조 (테스트만 권한 매트릭스 밖에서 직접 조작)
    (vdir / "out.csv").write_text("tampered", encoding="utf-8")
    assert run.run("t91_downstream", "before") == run.EXIT_UPSTREAM


def test_하류_manifest에_상류_버전_해시_고정(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert run.run("t91_downstream", "before") == run.EXIT_OK
    m = manifest.read_manifest(paths.artifact_dir("t91_downstream", "before"))
    art = [e for e in m["inputs"] if "artifact" in e]
    assert art[0]["artifact"] == "t90_dummy/before"
    assert art[0]["version"] == "v001"
    assert art[0]["files"]["out.csv"].startswith("sha256:")


def test_STALE_전파(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert run.run("t91_downstream", "before") == run.EXIT_OK
    assert status.stage_status("t91_downstream", "before")["status"] == "OK"
    # 상류 재게시(파라미터 더미 변경) → 하류 STALE (design.md §4.3-7)
    sandbox["state"]["params"]["t90_dummy"] = {"v": 99}
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    st = status.stage_status("t91_downstream", "before")
    assert st["status"] == "STALE"
    assert "v001 → v002" in st["stale_because"][0]


def test_UNEXPLAINED_하류_전파_배지(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "diff_unexplained"}
    assert run.run("t90_dummy", "before") == run.EXIT_UNEXPLAINED
    sandbox["state"]["params"]["t91_downstream"] = {}
    assert run.run("t91_downstream", "before") == run.EXIT_OK
    m = manifest.read_manifest(paths.artifact_dir("t91_downstream", "before"))
    assert m["upstream_unexplained"] == ["t90_dummy/before@v001"]
    assert status.stage_status("t91_downstream", "before")["unexplained_badge"]


def test_CLI_main_경로(sandbox):
    # argparse 경유 실동작: run / promote 서브커맨드 (--ack에는 --reviewed-by 필수)
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.main(["t90_dummy", "--scope", "before"]) == run.EXIT_PHYSICAL
    assert run.main(["promote", "t90_dummy", "before", "v001",
                     "--ack", "P-DUM-X-001"]) == run.EXIT_PHYSICAL   # 익명 ack 거부
    assert run.main(["promote", "t90_dummy", "before", "v001",
                     "--ack", "P-DUM-X-001", "--reviewed-by", "whtnm"]) == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"


def test_빌드_중_ContractViolation은_exit2_rejected_기록(sandbox):
    # 빌드 중 로더 계약 위반이 traceback exit 1로 새면 runs/ 기록도 rejected 개명도 없이
    # 고아 vNNN이 남는다 — exit 규약 {0,2,3,4,5} 준수 확인 (검증 라운드 1 수리)
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "build_contract_violation"}
    assert run.run("t90_dummy", "before") == run.EXIT_CONTRACT
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    names = [d.name for d in sdir.iterdir() if d.is_dir()]
    assert names == ["v001-rejected"]                       # 고아 vNNN 없음
    recs = list(paths.RUNS.glob("*_t90_dummy_before*.json"))
    assert len(recs) == 1
    rec = json.loads(recs[0].read_text(encoding="utf-8"))
    assert rec["exit_code"] == run.EXIT_CONTRACT
    assert "build_contract_violation" in rec["note"]


def test_ack_기록에_주체와_시각(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before", acks=["P-DUM-X-001"],
                   reviewed_by="whtnm") == run.EXIT_OK
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["acks"][0]["by"] == "whtnm"
    assert m["acks"][0]["at"]                               # 시각 기록 (design.md §4.2)


def test_review_overrides_게이트는_reviewed_by를_요구(sandbox, tmp_path):
    # design.md §4.3-3 — override 데이터 행이 실린 빌드는 --reviewed-by 없이는 보류.
    # Stage 1에서 휴면이던 경로의 실동작 검증 (검증 라운드 1 지적).
    import os
    ov = tmp_path / "ov.csv"
    ov.write_text("a,b\n1,2\n", encoding="utf-8")           # 헤더 + 데이터 1행
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL   # 보류
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending"
    assert m["needs_review"] is True                        # 빌드 시점 술어 고정 기록
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["reviewed_by"] == "whtnm"


def test_review_게이트_TOCTOU_빌드후_원복해도_우회_불가(sandbox, tmp_path):
    # 검증 라운드 1 (Stage 2) major 수리 — 실증된 우회 시나리오의 재현 봉쇄:
    # override 1행 실린 빌드(pending) → 파일을 빈 헤더로 원복 → --reviewed-by 없는 promote는
    # 여전히 보류여야 한다 (술어 = 버전 자신의 빌드 시점 기록, 현재 디스크 파일 아님).
    import os
    ov = tmp_path / "ov.csv"
    ov.write_text("a,b\n1,2\n", encoding="utf-8")
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL   # 보류 (정상)
    ov.write_text("a,b\n", encoding="utf-8")                # 빈 헤더로 원복 (우회 시도)
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL   # 차단
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending"
    assert m["reviewed_by"] is None
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK      # 정식 경로
    assert manifest.read_manifest(
        paths.artifact_dir("t90_dummy", "before"))["reviewed_by"] == "whtnm"


def test_review_게이트_역방향_빈_override_빌드는_현재_파일_주입에_불변(sandbox, tmp_path):
    # 동일 근원의 역방향 오류 수리 — 빈 override로 빌드된 버전은, promote 시점의 파일에
    # 데이터 행이 실려 있어도 그 버전의 판정이 바뀌지 않는다 (needs_review=False 기록).
    import os
    ov = tmp_path / "ov.csv"
    ov.write_text("a,b\n", encoding="utf-8")                # 빈 헤더로 빌드
    st = run.REGISTRY["t90_dummy"]
    st.review_overrides.append(os.path.relpath(ov, paths.ROOT))
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}   # pending 유도
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["needs_review"] is False
    ov.write_text("a,b\n1,2\n", encoding="utf-8")           # promote 전 데이터 행 주입
    assert run._needs_review(st, "before") is True          # 현재 파일 기준으로는 True지만
    assert run._version_needs_review(m, st, "before") is False   # 버전 판정은 빌드 기록
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by="whtnm") == run.EXIT_OK


def test_review_게이트_구버전_manifest_폴백은_보수적(sandbox, tmp_path):
    # needs_review 필드가 없는 구버전 manifest: 핀 부재/해시 불일치 시 판정 불능 →
    # 보수적으로 --reviewed-by 요구 (무심사 게시 금지).
    import os
    ov = tmp_path / "ov.csv"
    ov.write_text("a,b\n1,2\n", encoding="utf-8")
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    vdir = paths.ARTIFACTS / "t90_dummy" / "before" / "v001"
    m = manifest.read_manifest(vdir)
    del m["needs_review"]                                   # 구버전 manifest 시뮬레이션
    (vdir / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    ov.write_text("a,b\n", encoding="utf-8")                # 원복 (해시 드리프트)
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL   # 차단
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK


def test_promote_경로도_UNEXPLAINED면_exit4(sandbox):
    # 검증 라운드 1 (Stage 2) minor 수리 — pending→promote 흐름에서 exit 4 의미론
    # (verification.md §3.2)이 exit code 채널에서 유실되지 않는다 (게시 자체는 비차단).
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail+diff_unexplained"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"],
                               reviewed_by="whtnm") == run.EXIT_UNEXPLAINED
    assert paths.latest_version("t90_dummy", "before") == "v001"   # 게시은 됨
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["status"] == "promoted"
    assert any("promoted_via_promote_unexplained" in n for n in _run_notes())


def test_promote_ack_기록은_phys_fail_교집합만(sandbox):
    # 검증 라운드 1 (Stage 2) minor 수리 — run 경로(교집합 기록)와의 비대칭 해소:
    # 존재하지 않는 check_id의 ack은 판정 이력(manifest.acks)에 남지 않는다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001", "P-BOGUS-X-999"],
                               reviewed_by="whtnm") == run.EXIT_OK
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert [a["check_id"] for a in m["acks"]] == ["P-DUM-X-001"]   # 교집합만
    assert m["checks_summary"]["PHYSICAL"]["acked"] == 1


def test_promote_직전_산출물_변조는_exit5_거부(sandbox):
    # 검증 라운드 1 (Stage 2) minor 수리 — 빌드~promote 사이 변조 창 봉쇄:
    # promote는 게시 직전 출력 해시를 재검증하고 불일치면 거부한다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    vdir = paths.ARTIFACTS / "t90_dummy" / "before" / "v001"
    (vdir / "out.csv").write_text("tampered", encoding="utf-8")
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"],
                               reviewed_by="whtnm") == run.EXIT_UPSTREAM
    m = manifest.read_manifest(vdir)
    assert m["status"] == "pending"                         # 게시되지 않음
    assert not (paths.ARTIFACTS / "t90_dummy" / "before" / "_latest.json").exists()
    assert any("promote_outputs_corrupt" in n for n in _run_notes())


def test_runs_기록은_게시_여부와_무관(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "contract_fail"}
    run.run("t90_dummy", "before")
    recs = list(paths.RUNS.glob("*_t90_dummy_before*.json"))
    assert len(recs) == 1
    rec = json.loads(recs[0].read_text(encoding="utf-8"))
    assert rec["exit_code"] == run.EXIT_CONTRACT


def _run_notes(stage="t90_dummy", scope="before"):
    recs = sorted(paths.RUNS.glob(f"*_{stage}_{scope}*.json"))
    return [json.loads(r.read_text(encoding="utf-8"))["note"] for r in recs]


def test_promote_서브커맨드도_runs에_기록(sandbox):
    # verification.md §3.3 — 게시 여부와 무관하게 '전 실행' 기록 (검증 라운드 2 수리)
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by=None) == run.EXIT_PHYSICAL
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by="whtnm") == run.EXIT_OK
    notes = _run_notes()
    assert len(notes) == 3                                  # run 1 + promote 2
    assert any("promote_anonymous_ack_refused" in n for n in notes)
    assert any("promoted_via_promote" in n for n in notes)


def test_동결층_원복_flip_flop은_latest_동기화(sandbox, tmp_path):
    # design.md §4.3-5 — 입력 원복으로 구버전을 재사용하면 _latest도 그 버전으로
    # 동기화된다(하류가 현재 입력과 다른 내용을 읽는 flip-flop 해소, 검증 라운드 2 수리).
    import os
    frozen = tmp_path / "frozen.yaml"
    frozen.write_text("k: 1\n", encoding="utf-8")
    run.REGISTRY["t90_dummy"].config_files.append(os.path.relpath(frozen, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"
    frozen.write_text("k: 2\n", encoding="utf-8")           # 동결층 변경 → 새 버전
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v002"
    frozen.write_text("k: 1\n", encoding="utf-8")           # 원복 → v001 재사용
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"   # _latest 동기화
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    assert sorted(d.name for d in sdir.iterdir() if d.is_dir()) == ["v001", "v002"]
    assert any("latest_synced" in n for n in _run_notes())


def test_STALE_file_입력_해시_드리프트(sandbox, tmp_path):
    # STALE이 artifact 입력만 아니라 file 입력(raw·params·rules·overrides·동결층)
    # 드리프트도 검사한다 (검증 라운드 2 수리)
    import os
    cfg = tmp_path / "rules.yaml"
    cfg.write_text("r: 1\n", encoding="utf-8")
    rel = os.path.relpath(cfg, paths.ROOT)
    run.REGISTRY["t90_dummy"].config_files.append(rel)
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert status.stage_status("t90_dummy", "before")["status"] == "OK"
    cfg.write_text("r: 2\n", encoding="utf-8")              # 게시 후 config 변경
    st = status.stage_status("t90_dummy", "before")
    assert st["status"] == "STALE"
    assert any("해시 드리프트" in w and rel in w for w in st["stale_because"])
    cfg.unlink()                                            # 파일 소실도 STALE
    st = status.stage_status("t90_dummy", "before")
    assert st["status"] == "STALE"
    assert any("소실" in w for w in st["stale_because"])


def test_STALE_디렉터리_입력_드리프트(sandbox, tmp_path):
    # input_files의 디렉터리 입력(evidence/ 류)도 파일 추가/변경이 STALE로 드러난다
    import os
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "a.json").write_text("{}", encoding="utf-8")
    run.REGISTRY["t90_dummy"].input_files["before"] = [os.path.relpath(ev, paths.ROOT)]
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert status.stage_status("t90_dummy", "before")["status"] == "OK"
    (ev / "b.json").write_text("{}", encoding="utf-8")      # 파일 추가
    st = status.stage_status("t90_dummy", "before")
    assert st["status"] == "STALE"
    assert any("해시 드리프트" in w for w in st["stale_because"])
