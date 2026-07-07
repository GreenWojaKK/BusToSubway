# s02 override review 테스트 — override 데이터 행이 있으면 reviewer 기록이 필요하다.
# (design.md §4.3-3, framework_api.md §1.3, stage2_place_hub_spec.md §6.3)
import os

import pandas as pd

import bts.paths as paths
import bts.run as run


def _write_override(path, data_rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["action,place_a,place_b,reason,source"]
    lines += [",".join(r) for r in data_rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def test_default_overrides_are_empty_so_review_not_required():
    st = run.REGISTRY["s02_place"]
    assert run._needs_review(st, "before") is False
    assert run._needs_review(st, "after") is False


def test_scope_template_with_data_row_requires_review(monkeypatch, tmp_path):
    st = run.REGISTRY["s02_place"]
    base = tmp_path / "src/bts/config/overrides"
    _write_override(base / "place_overrides.before.csv",
                    [("merge", "PB_aaaaaaaa", "PB_bbbbbbbb", "r", "s")])
    _write_override(base / "place_overrides.after.csv", [])
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    assert run._needs_review(st, "before") is True         # 데이터 1행이면 review가 필요하다.
    assert run._needs_review(st, "after") is False         # {scope} 치환 확인 — after는 빈 헤더


def test_override_row_build_requires_reviewer(sandbox, tmp_path):
    # s02와 같은 review_overrides 설정을 더미 스테이지에 주입해 runner 경로를 확인한다.
    ov = tmp_path / "place_overrides.before.csv"
    _write_override(ov, [("merge", "PB_aaaaaaaa", "PB_bbbbbbbb", "r", "s")])
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL          # review 대기
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK
    import bts.manifest as manifest
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["reviewed_by"] == "whtnm"


def test_review_requirement_survives_file_revert(sandbox, tmp_path):
    # 검증 라운드 1 (Stage 2) major 수리 — s02 실증 우회 절차의 재현 봉쇄:
    # override 1행으로 빌드된 버전은 파일을 빈 헤더로 되돌려도 review 없이 promote할 수 없다.
    ov = tmp_path / "place_overrides.before.csv"
    _write_override(ov, [("merge", "PB_aaaaaaaa", "PB_bbbbbbbb", "r", "s")])
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    _write_override(ov, [])                                  # 빈 헤더로 되돌린다.
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL
    import bts.manifest as manifest
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending" and m["needs_review"] is True
    assert run.publish_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK


def test_registry_배선은_spec_3절과_일치():
    st = run.REGISTRY["s02_place"]
    assert st.inputs == ["s00_ingest"]                     # s01과 독립 — 배선이 곧 계약
    assert st.scopes == ("before", "after") or set(("before", "after")) <= set(st.scopes)
    assert "src/bts/config/overrides/place_overrides.{scope}.csv" in st.config_files
    assert "src/bts/config/overrides/name_aliases.csv" in st.config_files
    assert st.review_overrides == ["src/bts/config/overrides/place_overrides.{scope}.csv"]
    assert not st.input_files                              # raw 직접 읽기 없음
