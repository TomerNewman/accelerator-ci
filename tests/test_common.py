from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from accelerator_ci.cluster_provision.common import DeployError, run


class TestDeployError:
    def test_is_runtime_error(self):
        assert issubclass(DeployError, RuntimeError)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(DeployError, match="boom"):
            raise DeployError("boom")


class TestRun:
    @patch("accelerator_ci.cluster_provision.common.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(["echo"], 0, stdout="ok")
        result = run(["echo", "hi"])
        assert result.returncode == 0
        mock_run.assert_called_once_with(
            ["echo", "hi"], check=True, capture_output=False, text=True, env=None,
        )

    @patch("accelerator_ci.cluster_provision.common.subprocess.run")
    def test_capture_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(["ls"], 0)
        run(["ls"], capture_output=True)
        _, kwargs = mock_run.call_args
        assert kwargs["capture_output"] is True

    @patch("accelerator_ci.cluster_provision.common.subprocess.run")
    def test_env_passed_through(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(["x"], 0)
        run(["x"], env={"FOO": "bar"})
        _, kwargs = mock_run.call_args
        assert kwargs["env"] == {"FOO": "bar"}

    @patch("accelerator_ci.cluster_provision.common.subprocess.run")
    def test_failure_raises_deploy_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ["bad"], output="out", stderr="err",
        )
        with pytest.raises(DeployError, match="bad") as exc_info:
            run(["bad"])
        assert "STDOUT" in str(exc_info.value)
        assert "STDERR" in str(exc_info.value)

    @patch("accelerator_ci.cluster_provision.common.subprocess.run")
    def test_failure_no_output(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, ["x"])
        with pytest.raises(DeployError) as exc_info:
            run(["x"])
        assert "STDOUT" not in str(exc_info.value)

    @patch("accelerator_ci.cluster_provision.common.subprocess.run")
    def test_check_false(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(["x"], 1)
        result = run(["x"], check=False)
        assert result.returncode == 1
