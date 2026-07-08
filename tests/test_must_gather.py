from __future__ import annotations

import subprocess
from unittest.mock import patch

from accelerator_ci.cluster_provision.must_gather import (
    run_must_gather,
    run_must_gather_remote,
)


class TestRunMustGather:
    @patch("accelerator_ci.cluster_provision.must_gather.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        rc = run_must_gather("/kc", "/artifacts")
        assert rc == 0
        _, kwargs = mock_run.call_args
        assert kwargs["env"]["KUBECONFIG"] == "/kc"
        assert kwargs["env"]["ARTIFACT_DIR"] == "/artifacts"

    @patch("accelerator_ci.cluster_provision.must_gather.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 1)
        assert run_must_gather("/kc", "/art") == 1


class TestRunMustGatherRemote:
    @patch("accelerator_ci.cluster_provision.must_gather.close_ssh_multiplexing")
    @patch("accelerator_ci.cluster_provision.must_gather.subprocess.Popen")
    @patch("accelerator_ci.cluster_provision.must_gather.get_ssh_opts", return_value="-o Foo")
    @patch("accelerator_ci.cluster_provision.must_gather.scp_cmd")
    @patch("accelerator_ci.cluster_provision.must_gather.ssh_cmd")
    def test_mktemp_failure(self, mock_ssh, mock_scp, mock_opts, mock_popen, mock_close):
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="fail")
        rc = run_must_gather_remote("host", "root", "/art")
        assert rc == 1
        mock_close.assert_called_once()
