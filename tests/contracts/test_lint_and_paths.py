# lint와 쓰기 권한 테스트 — 금지어와 쓰기 가능 경로를 확인한다.
import pytest

import bts.paths as paths
from bts.checks import lint


class TestLint:
    def test_규범_어휘_컬럼명_검출(self):
        cols = ["transfer_wait_s", "비합리_지수", "headway_s"]
        r = lint.check_names("C-S05-B-006", cols, "design.md §8")
        assert r.status == "FAIL"
        assert ("비합리_지수", "비합리") in [tuple(v) for v in r.observed["violations"]]

    def test_영문_규범_어휘_검출(self):
        assert lint.find_forbidden(["excessive_wait"], None)

    def test_기술_어휘는_통과(self):
        cols = ["direct_unavailable_pairs", "gain", "wait_s"]
        assert lint.check_names("C-X", cols, "t").status == "PASS"

    def test_금칙어_목록은_params에서(self):
        # 금지어 목록은 코드가 아니라 params.yaml에서 읽는다.
        terms = lint.forbidden_terms()
        assert "비합리" in terms and "should" in terms


class TestWriteMatrix:
    def test_data_쓰기_거부(self):
        with pytest.raises(paths.WriteViolation):
            paths.assert_writable(paths.DATA / "x.csv")

    def test_reference_쓰기_거부(self):
        with pytest.raises(paths.WriteViolation):
            paths.assert_writable(paths.REFERENCE / "prior_baseline" / "baseline.yaml")

    def test_빌드_구간_밖_artifacts_쓰기_거부(self, sandbox):
        target = paths.ARTIFACTS / "t90_dummy" / "before" / "v001" / "out.csv"
        with pytest.raises(paths.WriteViolation):
            paths.assert_writable(target)

    def test_빌드_구간_안은_자기_vdir만_허용(self, sandbox):
        vdir = paths.ARTIFACTS / "t90_dummy" / "before" / "v001"
        other = paths.ARTIFACTS / "t90_dummy" / "before" / "v000" / "f.csv"
        with paths.build_context(vdir):
            paths.assert_writable(vdir / "out.csv")          # 현재 build 대상은 허용
            with pytest.raises(paths.WriteViolation):
                paths.assert_writable(other)                 # 다른 버전은 쓸 수 없다.

    def test_publish_context_allows_latest_only(self, sandbox):
        latest = paths.ARTIFACTS / "t90_dummy" / "before" / "_latest.json"
        with pytest.raises(paths.WriteViolation):
            paths.assert_writable(latest)                    # 일반 실행 중에는 쓸 수 없다.
        with paths.publish_context():
            paths.assert_writable(latest)                    # promote 중에만 허용

    def test_runs_docs는_허용(self):
        paths.assert_writable(paths.ROOT / "runs" / "r.json")
        paths.assert_writable(paths.DOCS / "investigations" / "DIFF-0001-x.md")


class TestVersionDirs:
    def test_버전_번호_증가와_rejected_격리(self, sandbox):
        v1 = paths.new_version_dir("t90_dummy", "before")
        assert v1.name == "v001"
        rej = paths.mark_rejected(v1)
        assert rej.name == "v001-rejected"
        v2 = paths.new_version_dir("t90_dummy", "before")
        assert v2.name == "v002"                             # rejected 번호는 재사용하지 않는다.

    def test_latest_부재는_UpstreamMissing(self, sandbox):
        with pytest.raises(paths.UpstreamMissing):
            paths.artifact_dir("t90_dummy", "before")
