# runner가 같은 입력을 재사용하고, review 요구와 반환 코드를 올바르게 처리하는지 검증한다.
# 더미 스테이지를 사용해 실제 stage 구현과 분리해서 확인한다.
import json

import paths
import manifest
import run
import status


def test_all_pass_records_latest_exit0(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    vdir = paths.artifact_dir("t90_dummy", "before")
    assert vdir.name == "v001"
    m = manifest.read_manifest(vdir)
    assert m["status"] == "published"
    assert (vdir / "checks.json").exists()
    assert m["content_key"].startswith("sha256:")


def test_same_inputs_reuse_existing_version(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert run.run("t90_dummy", "before") == run.EXIT_OK   # 같은 입력으로 다시 실행
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    versions = [d.name for d in sdir.iterdir() if d.is_dir()]
    assert versions == ["v001"]                             # 같은 내용이면 기존 버전을 재사용


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
    assert names == ["v001-rejected"]                       # 실패한 출력은 rejected로 남긴다.
    assert not (sdir / "_latest.json").exists()             # 최신 버전 포인터는 만들지 않는다.
    m = manifest.read_manifest(sdir / "v001-rejected")
    assert m["status"] == "rejected"


def test_physical_without_ack_returns_exit3_pending(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    assert not (sdir / "_latest.json").exists()
    m = manifest.read_manifest(sdir / "v001")
    assert m["status"] == "pending"


def test_physical_ack_with_reviewer_publishes(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    # run publish <stage> <scope> <version> --ack ... 경로에서도 승인자를 요구한다.
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by="whtnm") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["acks"][0]["check_id"] == "P-DUM-X-001"
    assert m["acks"][0]["by"] == "whtnm"


def test_publish_without_reviewer_rejects_ack(sandbox):
    # --ack에 --reviewed-by가 없으면 승인 기록을 남기지 않는다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by=None) == run.EXIT_PHYSICAL
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending"                         # 아직 확정하지 않는다.
    assert m["acks"] == []                                  # 승인자 없는 ack은 기록하지 않는다.
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    assert not (sdir / "_latest.json").exists()


def test_inline_ack_with_reviewer_publishes(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before",
                   acks=["P-DUM-X-001"], reviewed_by="whtnm") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"


def test_inline_ack_without_reviewer_stays_pending(sandbox):
    # run 경로에서도 --ack만으로는 승인 처리하지 않는다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before", acks=["P-DUM-X-001"]) == run.EXIT_PHYSICAL
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending"
    assert m["acks"] == []


def test_unexplained_diff_publishes_and_returns_exit4_with_note(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "diff_unexplained"}
    assert run.run("t90_dummy", "before") == run.EXIT_UNEXPLAINED
    assert paths.latest_version("t90_dummy", "before") == "v001"   # 산출물은 확정된다.
    stubs = list(sandbox["stub_dir"].glob("DIFF-*.md"))
    assert len(stubs) == 1                                          # 조사 메모를 만든다.
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["checks_summary"]["DIFF"]["unexplained"] == 1


def test_상류_부재는_exit5(sandbox):
    assert run.run("t91_downstream", "before") == run.EXIT_UPSTREAM


def test_상류_출력_변경은_exit5(sandbox):
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    vdir = paths.artifact_dir("t90_dummy", "before")
    # 확정된 artifact를 테스트에서 직접 바꾼다.
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
    # 상류를 다른 파라미터로 다시 확정하면 하류는 STALE이 된다.
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
    # CLI 경로에서도 run/publish가 같은 승인 규칙을 따른다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.main(["t90_dummy", "--scope", "before"]) == run.EXIT_PHYSICAL
    assert run.main(["publish", "t90_dummy", "before", "v001",
                     "--ack", "P-DUM-X-001"]) == run.EXIT_PHYSICAL   # 익명 ack 미적용
    assert run.main(["publish", "t90_dummy", "before", "v001",
                     "--ack", "P-DUM-X-001", "--reviewed-by", "whtnm"]) == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"


def test_빌드_중_ContractViolation은_exit2_rejected_기록(sandbox):
    # build 중 ContractViolation이 나도 traceback으로 끝나지 않고 rejected 버전과 실행 기록을 남긴다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "build_contract_violation"}
    assert run.run("t90_dummy", "before") == run.EXIT_CONTRACT
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    names = [d.name for d in sdir.iterdir() if d.is_dir()]
    assert names == ["v001-rejected"]                       # 중간 버전 디렉터리를 남기지 않는다.
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
    assert m["acks"][0]["at"]                               # 승인 시각을 기록한다.


def test_review_overrides_require_reviewer(sandbox, tmp_path):
    # override 데이터 행이 포함된 버전은 --reviewed-by 없이는 확정할 수 없다.
    import os
    ov = tmp_path / "ov.csv"
    ov.write_text("a,b\n1,2\n", encoding="utf-8")           # 헤더 + 데이터 1행
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL   # review 대기
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending"
    assert m["needs_review"] is True                        # 빌드 시점의 review 필요 여부를 기록한다.
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["reviewed_by"] == "whtnm"


def test_review_requirement_uses_build_record_after_file_revert(sandbox, tmp_path):
    # override 1행으로 만든 버전은 이후 파일을 빈 헤더로 되돌려도 review 없이 확정할 수 없다.
    # 판단은 현재 파일 상태가 아니라 해당 버전의 빌드 기록을 기준으로 한다.
    import os
    ov = tmp_path / "ov.csv"
    ov.write_text("a,b\n1,2\n", encoding="utf-8")
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL   # review 대기
    ov.write_text("a,b\n", encoding="utf-8")                # 빈 헤더로 되돌린다.
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL   # 차단
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending"
    assert m["reviewed_by"] is None
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK      # reviewer를 명시하면 통과
    assert manifest.read_manifest(
        paths.artifact_dir("t90_dummy", "before"))["reviewed_by"] == "whtnm"


def test_empty_override_build_ignores_later_file_rows(sandbox, tmp_path):
    # 빈 override로 만든 버전은 이후 파일에 데이터 행이 생겨도 그 버전의 review 필요 여부가 바뀌지 않는다.
    import os
    ov = tmp_path / "ov.csv"
    ov.write_text("a,b\n", encoding="utf-8")                # 빈 헤더로 빌드한다.
    st = run.REGISTRY["t90_dummy"]
    st.review_overrides.append(os.path.relpath(ov, paths.ROOT))
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}   # pending 유도
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["needs_review"] is False
    ov.write_text("a,b\n1,2\n", encoding="utf-8")           # publish 전에 데이터 행을 추가한다.
    assert run._needs_review(st, "before") is True          # 현재 파일만 보면 review가 필요하지만
    assert run._version_needs_review(m, st, "before") is False   # 이 버전은 빌드 기록을 따른다.
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by="whtnm") == run.EXIT_OK


def test_old_manifest_requires_reviewer_when_review_state_unknown(sandbox, tmp_path):
    # needs_review 필드가 없는 manifest에서는 판단 근거가 부족하면 --reviewed-by를 요구한다.
    import os
    ov = tmp_path / "ov.csv"
    ov.write_text("a,b\n1,2\n", encoding="utf-8")
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    vdir = paths.ARTIFACTS / "t90_dummy" / "before" / "v001"
    m = manifest.read_manifest(vdir)
    del m["needs_review"]                                   # 오래된 manifest 형식을 흉내 낸다.
    (vdir / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    ov.write_text("a,b\n", encoding="utf-8")                # 입력 파일 내용이 달라진 상태
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL   # 차단
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK


def test_publish_path_returns_exit4_when_unexplained(sandbox):
    # publish가 성공해도 설명되지 않은 차이가 있으면 반환 코드에는 그 상태가 남아야 한다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail+diff_unexplained"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"],
                               reviewed_by="whtnm") == run.EXIT_UNEXPLAINED
    assert paths.latest_version("t90_dummy", "before") == "v001"   # 버전은 확정된다.
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["status"] == "published"
    assert any("published_via_publish_unexplained" in n for n in _run_notes())


def test_publish_ack_records_only_matching_physical_failures(sandbox):
    # 존재하지 않는 check_id에 대한 ack은 manifest.acks에 남기지 않는다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001", "P-BOGUS-X-999"],
                               reviewed_by="whtnm") == run.EXIT_OK
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert [a["check_id"] for a in m["acks"]] == ["P-DUM-X-001"]   # 실제 실패 항목만 기록
    assert m["checks_summary"]["PHYSICAL"]["acked"] == 1


def test_publish_rechecks_outputs_before_latest_update(sandbox):
    # build 이후 publish 전에 출력이 바뀌면 publish 직전 해시 검사에서 거부한다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    vdir = paths.ARTIFACTS / "t90_dummy" / "before" / "v001"
    (vdir / "out.csv").write_text("tampered", encoding="utf-8")
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"],
                               reviewed_by="whtnm") == run.EXIT_UPSTREAM
    m = manifest.read_manifest(vdir)
    assert m["status"] == "pending"                         # 아직 확정하지 않는다.
    assert not (paths.ARTIFACTS / "t90_dummy" / "before" / "_latest.json").exists()
    assert any("publish_outputs_changed" in n for n in _run_notes())


def test_runs_record_is_written_for_every_result(sandbox):
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "contract_fail"}
    run.run("t90_dummy", "before")
    recs = list(paths.RUNS.glob("*_t90_dummy_before*.json"))
    assert len(recs) == 1
    rec = json.loads(recs[0].read_text(encoding="utf-8"))
    assert rec["exit_code"] == run.EXIT_CONTRACT


def _run_notes(stage="t90_dummy", scope="before"):
    recs = sorted(paths.RUNS.glob(f"*_{stage}_{scope}*.json"))
    return [json.loads(r.read_text(encoding="utf-8"))["note"] for r in recs]


def test_publish_subcommand_records_run(sandbox):
    # publish 여부와 관계없이 모든 실행 기록이 남아야 한다.
    sandbox["state"]["params"]["t90_dummy"] = {"mode": "physical_fail"}
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by=None) == run.EXIT_PHYSICAL
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=["P-DUM-X-001"], reviewed_by="whtnm") == run.EXIT_OK
    notes = _run_notes()
    assert len(notes) == 3                                  # run 1 + publish 2
    assert any("publish_anonymous_ack_refused" in n for n in notes)
    assert any("published_via_publish" in n for n in notes)


def test_reused_old_input_version_updates_latest(sandbox, tmp_path):
    # 입력을 예전 내용으로 되돌려 기존 버전을 재사용하면 _latest도 그 버전을 가리켜야 한다.
    import os
    frozen = tmp_path / "frozen.yaml"
    frozen.write_text("k: 1\n", encoding="utf-8")
    run.REGISTRY["t90_dummy"].config_files.append(os.path.relpath(frozen, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"
    frozen.write_text("k: 2\n", encoding="utf-8")           # 입력 내용 변경 → 새 버전
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v002"
    frozen.write_text("k: 1\n", encoding="utf-8")           # 예전 내용으로 되돌림 → v001 재사용
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert paths.latest_version("t90_dummy", "before") == "v001"   # _latest도 재사용 버전을 가리킴
    sdir = paths.ARTIFACTS / "t90_dummy" / "before"
    assert sorted(d.name for d in sdir.iterdir() if d.is_dir()) == ["v001", "v002"]
    assert any("latest_synced" in n for n in _run_notes())


def test_stale_detects_file_input_hash_change(sandbox, tmp_path):
    # file 입력(raw·params·rules·overrides·reference files)이 바뀌어도 STALE로 표시한다.
    import os
    cfg = tmp_path / "rules.yaml"
    cfg.write_text("r: 1\n", encoding="utf-8")
    rel = os.path.relpath(cfg, paths.ROOT)
    run.REGISTRY["t90_dummy"].config_files.append(rel)
    assert run.run("t90_dummy", "before") == run.EXIT_OK
    assert status.stage_status("t90_dummy", "before")["status"] == "OK"
    cfg.write_text("r: 2\n", encoding="utf-8")              # 확정 후 config 변경
    st = status.stage_status("t90_dummy", "before")
    assert st["status"] == "STALE"
    assert any("해시 드리프트" in w and rel in w for w in st["stale_because"])
    cfg.unlink()                                            # 파일이 없어져도 STALE
    st = status.stage_status("t90_dummy", "before")
    assert st["status"] == "STALE"
    assert any("소실" in w for w in st["stale_because"])


def test_stale_detects_directory_input_hash_change(sandbox, tmp_path):
    # input_files가 디렉터리를 가리키는 경우 파일 추가/변경도 STALE로 드러난다.
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
