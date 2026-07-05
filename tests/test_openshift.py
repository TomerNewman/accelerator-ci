from __future__ import annotations

import pytest

from accelerator_ci.cluster_provision.openshift import get_latest_ocp_version


class TestGetLatestOcpVersion:
    def test_invalid_version_format(self):
        with pytest.raises(ValueError, match="Invalid version tag"):
            get_latest_ocp_version("4.20.1")

    def test_invalid_version_letters(self):
        with pytest.raises(ValueError, match="Invalid version tag"):
            get_latest_ocp_version("abc")

    def test_unsupported_channel(self):
        with pytest.raises(ValueError, match="not supported"):
            get_latest_ocp_version("4.20", channel_name="candidate")

    def test_empty_version(self):
        with pytest.raises(ValueError, match="Invalid version tag"):
            get_latest_ocp_version("")
