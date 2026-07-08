from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from accelerator_ci.shared.oc_runner import (
    _is_transient,
    _retry_loop,
    LocalOcRunner,
    RemoteOcRunner,
)


def _result(rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class TestIsTransient:
    def test_success_is_not_transient(self):
        assert _is_transient(_result(0)) is False

    def test_rc_124_is_transient(self):
        assert _is_transient(_result(124)) is True

    @pytest.mark.parametrize("msg", [
        "connection refused",
        "Unable to connect to the server",
        "context deadline exceeded",
        "etcd leader changed",
        "TLS handshake timeout",
        "HTTP 503 Service Unavailable",
        "command timed out",
    ])
    def test_known_patterns(self, msg):
        assert _is_transient(_result(1, stderr=msg)) is True

    def test_unknown_error_is_not_transient(self):
        assert _is_transient(_result(1, stderr="permission denied")) is False

    def test_pattern_in_stdout(self):
        assert _is_transient(_result(1, stdout="connection refused")) is True


class TestRetryLoop:
    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    def test_succeeds_first_try(self, _):
        calls = []
        def run_fn():
            calls.append(1)
            return _result(0)
        _retry_loop(run_fn, retries=3)
        assert len(calls) == 1

    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    def test_retries_on_transient(self, _):
        attempts = []
        def run_fn():
            attempts.append(1)
            if len(attempts) < 3:
                return _result(1, stderr="connection refused")
            return _result(0)
        _retry_loop(run_fn, retries=3)
        assert len(attempts) == 3

    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    def test_gives_up_after_retries(self, _):
        def run_fn():
            return _result(1, stderr="connection refused")
        result = _retry_loop(run_fn, retries=2)
        assert result.returncode == 1

    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    def test_non_transient_stops_immediately(self, _):
        calls = []
        def run_fn():
            calls.append(1)
            return _result(1, stderr="forbidden")
        _retry_loop(run_fn, retries=3)
        assert len(calls) == 1

    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    def test_negative_retries_treated_as_zero(self, _):
        calls = []
        def run_fn():
            calls.append(1)
            return _result(1, stderr="connection refused")
        _retry_loop(run_fn, retries=-5)
        assert len(calls) == 1


class TestLocalOcRunner:
    def test_missing_kubeconfig_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="not found"):
            LocalOcRunner(tmp_path / "nope.kubeconfig")

    @patch("accelerator_ci.shared.oc_runner.subprocess.run")
    def test_oc_success(self, mock_run, tmp_path):
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        mock_run.return_value = _result(0, stdout="ok")
        runner = LocalOcRunner(kc)
        result = runner.oc("get", "pods", retries=0)
        assert result.returncode == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "oc"

    @patch("accelerator_ci.shared.oc_runner.subprocess.run")
    def test_oc_timeout_returns_124(self, mock_run, tmp_path):
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        mock_run.side_effect = subprocess.TimeoutExpired(["oc"], 5)
        runner = LocalOcRunner(kc)
        result = runner.oc("get", "nodes", timeout=5, retries=0)
        assert result.returncode == 124

    @patch("accelerator_ci.shared.oc_runner.subprocess.run")
    def test_apply_yaml_success(self, mock_run, tmp_path):
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        mock_run.return_value = _result(0)
        runner = LocalOcRunner(kc)
        runner.apply_yaml("kind: Namespace")

    @patch("accelerator_ci.shared.oc_runner.subprocess.run")
    def test_apply_yaml_failure_raises(self, mock_run, tmp_path):
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        mock_run.return_value = _result(1, stderr="invalid yaml")
        runner = LocalOcRunner(kc)
        with pytest.raises(RuntimeError, match="apply failed"):
            runner.apply_yaml("bad")


class TestRemoteOcRunner:
    @patch("accelerator_ci.shared.oc_runner.ssh_cmd")
    def test_oc_wraps_ssh(self, mock_ssh):
        mock_ssh.return_value = _result(0, stdout="nodes")
        runner = RemoteOcRunner("host", "root", "/root/kubeconfig")
        result = runner.oc("get", "nodes", retries=0)
        assert result.returncode == 0
        assert result.stdout == "nodes"

    @patch("accelerator_ci.shared.oc_runner.ssh_cmd")
    @patch("accelerator_ci.shared.oc_runner.scp_cmd")
    def test_apply_yaml_scp_and_apply(self, mock_scp, mock_ssh):
        mock_ssh.return_value = _result(0)
        runner = RemoteOcRunner("host", "root", "/root/kubeconfig")
        runner.apply_yaml("kind: Pod")
        mock_scp.assert_called_once()
        assert mock_ssh.call_count >= 1

    @patch("accelerator_ci.shared.oc_runner.close_ssh_multiplexing")
    def test_close(self, mock_close):
        runner = RemoteOcRunner("host", "root", "/root/kubeconfig")
        runner.close()
        mock_close.assert_called_once_with("host", "root")
