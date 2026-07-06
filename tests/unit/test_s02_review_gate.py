# s02 override 리뷰 게이트 — override 데이터 행이 실리면 게시에 --reviewed-by 필수
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


def test_실리포_상태는_빈_헤더라서_게이트_비활성():
    st = run.REGISTRY["s02_place"]
    assert run._needs_review(st, "before") is False
    assert run._needs_review(st, "after") is False


def test_scope_치환과_데이터_1행_주입시_게이트_활성(monkeypatch, tmp_path):
    st = run.REGISTRY["s02_place"]
    base = tmp_path / "src/bts/config/overrides"
    _write_override(base / "place_overrides.before.csv",
                    [("merge", "PB_aaaaaaaa", "PB_bbbbbbbb", "r", "s")])
    _write_override(base / "place_overrides.after.csv", [])
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    assert run._needs_review(st, "before") is True         # 데이터 1행 → 게이트 활성
    assert run._needs_review(st, "after") is False         # {scope} 치환 확인 — after는 빈 헤더


def test_override_1행_실린_빌드는_reviewed_by_없이_보류(sandbox, tmp_path):
    # 러너 전 경로 실동작: s02와 동일한 review_overrides 배선을 더미 스테이지에 주입
    ov = tmp_path / "place_overrides.before.csv"
    _write_override(ov, [("merge", "PB_aaaaaaaa", "PB_bbbbbbbb", "r", "s")])
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL          # 보류 (exit 3)
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK
    import bts.manifest as manifest
    m = manifest.read_manifest(paths.artifact_dir("t90_dummy", "before"))
    assert m["reviewed_by"] == "whtnm"


def test_TOCTOU_빌드후_원복해도_reviewed_by_게이트_유지(sandbox, tmp_path):
    # 검증 라운드 1 (Stage 2) major 수리 — s02 실증 우회 절차의 재현 봉쇄:
    # override 1행 빌드(pending) → 파일 빈 헤더 원복 → 무심사 promote는 계속 차단.
    ov = tmp_path / "place_overrides.before.csv"
    _write_override(ov, [("merge", "PB_aaaaaaaa", "PB_bbbbbbbb", "r", "s")])
    run.REGISTRY["t90_dummy"].review_overrides.append(os.path.relpath(ov, paths.ROOT))
    assert run.run("t90_dummy", "before") == run.EXIT_PHYSICAL
    _write_override(ov, [])                                  # 빈 헤더로 원복 (우회 시도)
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by=None) == run.EXIT_PHYSICAL
    import bts.manifest as manifest
    m = manifest.read_manifest(paths.ARTIFACTS / "t90_dummy" / "before" / "v001")
    assert m["status"] == "pending" and m["needs_review"] is True
    assert run.promote_pending("t90_dummy", "before", "v001",
                               acks=[], reviewed_by="whtnm") == run.EXIT_OK


def test_registry_배선은_spec_3절과_일치():
    st = run.REGISTRY["s02_place"]
    assert st.inputs == ["s00_ingest"]                     # s01과 독립 — 배선이 곧 계약
    assert st.scopes == ("before", "after") or set(("before", "after")) <= set(st.scopes)
    assert "src/bts/config/overrides/place_overrides.{scope}.csv" in st.config_files
    assert "src/bts/config/overrides/name_aliases.csv" in st.config_files
    assert st.review_overrides == ["src/bts/config/overrides/place_overrides.{scope}.csv"]
    assert not st.input_files                              # raw 직접 읽기 없음
