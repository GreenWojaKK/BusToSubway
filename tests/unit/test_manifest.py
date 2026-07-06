# manifest — 해시·멱등 키·code_ref (design.md §4.2)
import bts.manifest as manifest


def test_sha256_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("bts", encoding="utf-8")
    h = manifest.sha256_file(p)
    assert len(h) == 64
    p2 = tmp_path / "b.txt"
    p2.write_text("bts", encoding="utf-8")
    assert manifest.sha256_file(p2) == h        # 내용 동일 = 해시 동일


def test_params_hash_정준화():
    a = manifest.params_hash({"b": 1, "a": 2})
    b = manifest.params_hash({"a": 2, "b": 1})
    assert a == b and a.startswith("sha256:")   # 키 순서 무관


def test_content_key_민감도():
    ri = manifest.ResolvedInputs(artifacts={"s00/before": {"version": "v001", "files": {"f": "h1"}}},
                                 files={"params.yaml": "h2"})
    k1 = manifest.content_key(ri, "sha256:p1", "git:abc")
    k2 = manifest.content_key(ri, "sha256:p2", "git:abc")   # params 변경
    ri2 = manifest.ResolvedInputs(artifacts={"s00/before": {"version": "v001", "files": {"f": "hX"}}},
                                  files={"params.yaml": "h2"})
    k3 = manifest.content_key(ri2, "sha256:p1", "git:abc")  # 입력 해시 변경
    assert len({k1, k2, k3}) == 3


def test_content_key는_상류_버전_라벨에_불변():
    # design.md §4.3-5 문언 정합 — 키의 입력은 (입력 해시 전부 + params_hash + code_ref)뿐.
    # 내용 동일 = 키 동일: 버전 라벨만 다른 상류 재게시은 하류 재실행을 유발하지 않는다.
    ri_v1 = manifest.ResolvedInputs(artifacts={"s00/before": {"version": "v001", "files": {"f": "h1"}}},
                                    files={"params.yaml": "h2"})
    ri_v2 = manifest.ResolvedInputs(artifacts={"s00/before": {"version": "v002", "files": {"f": "h1"}}},
                                    files={"params.yaml": "h2"})
    assert manifest.content_key(ri_v1, "sha256:p1", "git:abc") \
        == manifest.content_key(ri_v2, "sha256:p1", "git:abc")


def test_code_ref_형식():
    ref = manifest.code_ref()
    assert ref.startswith("git:") or ref.startswith("dirty+")


def test_sha256_dir_결정성과_민감도(tmp_path):
    d = tmp_path / "ev"
    d.mkdir()
    (d / "a.json").write_text('{"x":1}', encoding="utf-8")
    (d / "b.json").write_text('{"y":2}', encoding="utf-8")
    h1 = manifest.sha256_dir(d)
    assert h1 == manifest.sha256_dir(d)                 # 결정성
    (d / "b.json").write_text('{"y":3}', encoding="utf-8")
    assert manifest.sha256_dir(d) != h1                 # 내용 변경 감지
    (d / "c.json").write_text("{}", encoding="utf-8")
    assert manifest.sha256_dir(d) != h1                 # 파일 추가 감지


def test_resolve_inputs_동결층_포함(sandbox):
    # 동결층 3파일이 전 스테이지 입력에 해시 고정된다 — known_deviations 변경이
    # content_key를 바꿔 멱등 스킵을 우회하지 못하게 하는 핀 (검증 라운드 1 수리)
    ri = manifest.resolve_inputs("t90_dummy", "before")
    for rel in ("reference/prior_baseline/raw_hashes.yaml",
                "reference/prior_baseline/baseline.yaml",
                "reference/prior_baseline/known_deviations.yaml"):
        assert rel in ri.files


def test_manifest_entries_형태():
    ri = manifest.ResolvedInputs(
        artifacts={"s00_ingest/before": {"version": "v002", "files": {"stop_times.parquet": "sha256:x"}}},
        files={"src/bts/config/params.yaml": "abc"})
    entries = ri.manifest_entries()
    assert {"artifact": "s00_ingest/before", "version": "v002",
            "files": {"stop_times.parquet": "sha256:x"}} in entries
    assert {"file": "src/bts/config/params.yaml", "sha256": "abc"} in entries
