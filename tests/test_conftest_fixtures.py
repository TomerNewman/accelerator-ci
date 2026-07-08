from __future__ import annotations

from unittest.mock import patch

from kubernetes import config


class TestLoadKubeconfig:
    @patch.dict("os.environ", {"KUBECONFIG": "/my/kubeconfig"})
    @patch("kubernetes.config.load_kube_config")
    def test_kubeconfig_env(self, mock_load):
        from accelerator_ci.testing import conftest
        # Call the underlying function, not the fixture wrapper
        conftest.load_kubeconfig.__wrapped__()
        mock_load.assert_called_once_with(config_file="/my/kubeconfig")

    @patch.dict("os.environ", {}, clear=True)
    @patch("kubernetes.config.load_kube_config")
    @patch("kubernetes.config.load_incluster_config")
    def test_fallback_to_default(self, mock_incluster, mock_load):
        mock_incluster.side_effect = config.ConfigException("not in cluster")
        from accelerator_ci.testing import conftest
        conftest.load_kubeconfig.__wrapped__()
        mock_load.assert_called_once()
