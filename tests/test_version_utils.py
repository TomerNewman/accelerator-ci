from accelerator_ci.shared.version_utils import max_version


class TestMaxVersion:
    def test_second_higher(self):
        assert max_version("1.0.0", "2.0.0") == "2.0.0"

    def test_first_higher(self):
        assert max_version("3.1.0", "2.9.9") == "3.1.0"

    def test_equal(self):
        assert max_version("1.2.3", "1.2.3") == "1.2.3"

    def test_patch_comparison(self):
        assert max_version("4.20.3", "4.20.11") == "4.20.11"

    def test_prerelease(self):
        assert max_version("1.0.0-alpha", "1.0.0") == "1.0.0"
