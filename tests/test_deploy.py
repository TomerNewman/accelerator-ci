from __future__ import annotations

from unittest.mock import patch

import pytest

from accelerator_ci.cluster_provision.common import DeployError
from accelerator_ci.cluster_provision.deploy import (
    build_kcli_params,
    deploy_cluster,
    deploy_local,
)


class TestBuildKcliParams:
    def test_empty(self):
        assert build_kcli_params({}) == []

    def test_single(self):
        assert build_kcli_params({"cluster": "ocp"}) == ["-P", "cluster=ocp"]

    def test_multiple(self):
        result = build_kcli_params({"a": "1", "b": "2"})
        assert result == ["-P", "a=1", "-P", "b=2"]


class TestDeployCluster:
    @patch("accelerator_ci.cluster_provision.deploy.ensure_kcli_installed")
    def test_missing_pull_secret_raises(self, _):
        params = {"cluster": "c", "api_ip": "1.2.3.4", "domain": "lab",
                  "ctlplanes": "1", "workers": "0"}
        with pytest.raises(DeployError, match="pull_secret"):
            deploy_cluster(params, None, "root", 3600, None, None)

    @patch("accelerator_ci.cluster_provision.deploy.deploy_local")
    @patch("accelerator_ci.cluster_provision.deploy.ensure_pull_secret_exists")
    @patch("accelerator_ci.cluster_provision.deploy.ensure_kcli_installed")
    def test_local_dispatch(self, kcli, pull, local, tmp_path):
        secret = tmp_path / "ps.json"
        secret.write_text("{}")
        params = {"cluster": "c", "api_ip": "1.2.3.4", "domain": "lab",
                  "ctlplanes": "1", "workers": "0", "pull_secret": str(secret)}
        deploy_cluster(params, None, "root", 3600, None, None)
        local.assert_called_once()

    @patch("accelerator_ci.cluster_provision.deploy.deploy_remote")
    @patch("accelerator_ci.cluster_provision.deploy.ensure_pull_secret_exists")
    @patch("accelerator_ci.cluster_provision.deploy.ensure_kcli_installed")
    def test_remote_dispatch(self, kcli, pull, remote, tmp_path):
        secret = tmp_path / "ps.json"
        secret.write_text("{}")
        params = {"cluster": "c", "api_ip": "1.2.3.4", "domain": "lab",
                  "ctlplanes": "1", "workers": "0", "pull_secret": str(secret)}
        deploy_cluster(params, "remote-host", "root", 3600, None, None)
        remote.assert_called_once()


class TestDeployLocal:
    @patch("accelerator_ci.cluster_provision.deploy.run")
    @patch("accelerator_ci.cluster_provision.deploy.ensure_kcli_config")
    def test_runs_kcli_create(self, kcli_config, mock_run):
        params = {"cluster": "test", "ctlplanes": "1", "workers": "0"}
        deploy_local(params, ctlplanes=1, workers=0)
        cmd = mock_run.call_args[0][0]
        assert cmd[:4] == ["kcli", "create", "cluster", "openshift"]
