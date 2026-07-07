# manifest 테스트 — 입력 해시와 code_ref가 버전 재사용 판단에 쓰이는지 확인한다.
import bts.manifest as manifest


def test_sha256_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("bts", encoding="utf-8")
    h = manifest.sha256_file(p)
    assert len(h) == 64
    p2 = tmp_path / "b.txt"
    p2.write_text("bts", encoding="utf-8")
    assert manifest.sha256_file(p2) == h        # 내용이 같으면 해시도 같다.


def test_params_hash_정준화():
    a = manifest.params_hash({"b": 1, "a": 2})
    b = manifest.params_hash({"a": 2, "b": 1})
    assert a == b and a.startswith("sha256:")   # dict 키 순서와 무관하다.


def test_content_key_민감도():
    ri = manifest.ResolvedInputs(artifacts={"s00/before": {"version": "v001", "files": {"f": "h1"}}},
                                 files={"params.yaml": "h2"})
    k1 = manifest.content_key(ri, "sha256:p1", "git:abc")
    k2 = manifest.content_key(ri, "sha256:p2", "git:abc")   # params가 바뀐 경우
    ri2 = manifest.ResolvedInputs(artifacts={"s00/before": {"version": "v001", "files": {"f": "hX"}}},
                                  files={"params.yaml": "h2"})
    k3 = manifest.content_key(ri2, "sha256:p1", "git:abc")  # 입력 파일 해시가 바뀐 경우
    assert len({k1, k2, k3}) == 3


def test_content_key는_상류_버전_라벨에_불변():
    # 버전 재사용 기준은 입력 해시, params_hash, code_ref만 본다.
    # 내용이 같으면 상류 버전 라벨만 바뀌어도 하류 재실행은 필요 없다.
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
    assert h1 == manifest.sha256_dir(d)                 # 같은 디렉터리는 같은 해시
    (d / "b.json").write_text('{"y":3}', encoding="utf-8")
    assert manifest.sha256_dir(d) != h1                 # 파일 내용 변경 감지
    (d / "c.json").write_text("{}", encoding="utf-8")
    assert manifest.sha256_dir(d) != h1                 # 파일 추가 감지


def test_resolve_inputs_includes_reference_files(sandbox):
    # reference 3파일도 모든 스테이지의 입력 해시에 포함된다.
    # known_deviations가 바뀌면 같은 버전으로 재사용하지 않는다.
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
