from __future__ import annotations

from unittest.mock import patch

from accelerator_ci.cluster_provision.params import update_version_to_latest_patch


class TestUpdateVersionToLatestPatch:
    def test_empty_string_passthrough(self):
        assert update_version_to_latest_patch("") == ""

    def test_full_version_passthrough(self):
        assert update_version_to_latest_patch("4.16.3") == "4.16.3"

    @patch("accelerator_ci.cluster_provision.params.get_latest_ocp_version", return_value="4.16.8")
    def test_minor_resolves(self, _):
        assert update_version_to_latest_patch("4.16") == "4.16.8"

    @patch("accelerator_ci.cluster_provision.params.get_latest_ocp_version", return_value=None)
    def test_minor_no_match_returns_original(self, _):
        assert update_version_to_latest_patch("4.99") == "4.99"

    @patch("accelerator_ci.cluster_provision.params.get_latest_ocp_version", return_value="4.16")
    def test_minor_same_version(self, _):
        assert update_version_to_latest_patch("4.16") == "4.16"
