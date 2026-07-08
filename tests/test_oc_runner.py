from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from accelerator_ci.shared.oc_runner import LocalOcRunner, _is_transient


class TestIsTransient:
    def _result(self, rc: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=["oc"], returncode=rc, stdout=stdout, stderr=stderr)

    def test_success_is_not_transient(self):
        assert _is_transient(self._result(0)) is False

    def test_timeout_is_transient(self):
        assert _is_transient(self._result(124)) is True

    def test_connection_refused(self):
        assert _is_transient(self._result(1, stderr="dial tcp: connection refused")) is True

    def test_server_unavailable(self):
        assert _is_transient(self._result(1, stderr="the server is currently unable to handle the request")) is True

    def test_502_error(self):
        assert _is_transient(self._result(1, stderr="error: 502 Bad Gateway")) is True

    def test_503_error(self):
        assert _is_transient(self._result(1, stderr="503 Service Unavailable")) is True

    def test_etcd_leader_changed(self):
        assert _is_transient(self._result(1, stderr="etcd leader changed")) is True

    def test_tls_timeout(self):
        assert _is_transient(self._result(1, stderr="TLS handshake timeout")) is True

    def test_non_transient_error(self):
        assert _is_transient(self._result(1, stderr="error: the server doesn't have a resource type \"foobar\"")) is False

    def test_not_found(self):
        assert _is_transient(self._result(1, stderr="Error from server (NotFound): namespaces \"x\" not found")) is False

    def test_transient_in_stdout(self):
        assert _is_transient(self._result(1, stdout="unable to connect to the server")) is True

    def test_ssh_timeout_message(self):
        assert _is_transient(self._result(1, stderr="SSH command timed out after 300s")) is True

    def test_command_timed_out_message(self):
        assert _is_transient(self._result(1, stderr="Command timed out after 30s")) is True

    def test_context_deadline_exceeded(self):
        assert _is_transient(self._result(1, stderr="context deadline exceeded")) is True

    def test_no_route_to_host(self):
        assert _is_transient(self._result(1, stderr="dial tcp 10.0.0.1:6443: no route to host")) is True

    def test_unexpected_eof(self):
        assert _is_transient(self._result(1, stderr="unexpected EOF")) is True

    def test_broken_pipe(self):
        assert _is_transient(self._result(1, stderr="write: broken pipe")) is True

    def test_5xx_needs_word_boundary(self):
        assert _is_transient(self._result(1, stderr="port 65535 is invalid")) is False

    def test_5xx_in_port_number(self):
        assert _is_transient(self._result(1, stderr="listening on port 1500")) is False


class TestLocalOcRunnerRetry:
    @pytest.fixture
    def runner(self, tmp_path):
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        return LocalOcRunner(kc)

    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    @patch("subprocess.run")
    def test_retries_on_transient_then_succeeds(self, mock_run, mock_sleep, runner):
        transient = subprocess.CompletedProcess(
            args=["oc", "get", "nodes"], returncode=1,
            stdout="", stderr="unable to connect to the server",
        )
        success = subprocess.CompletedProcess(
            args=["oc", "get", "nodes"], returncode=0,
            stdout="node1 Ready", stderr="",
        )
        mock_run.side_effect = [transient, success]

        result = runner.oc("get", "nodes", retries=2)

        assert result.returncode == 0
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once()

    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    @patch("subprocess.run")
    def test_no_retry_on_non_transient(self, mock_run, mock_sleep, runner):
        error = subprocess.CompletedProcess(
            args=["oc", "get", "foobar"], returncode=1,
            stdout="", stderr="error: the server doesn't have a resource type \"foobar\"",
        )
        mock_run.return_value = error

        result = runner.oc("get", "foobar", retries=3)

        assert result.returncode == 1
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()

    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    @patch("subprocess.run")
    def test_exhausts_all_retries(self, mock_run, mock_sleep, runner):
        transient = subprocess.CompletedProcess(
            args=["oc", "get", "nodes"], returncode=1,
            stdout="", stderr="connection refused",
        )
        mock_run.return_value = transient

        result = runner.oc("get", "nodes", retries=2)

        assert result.returncode == 1
        assert mock_run.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("subprocess.run")
    def test_no_retry_when_retries_zero(self, mock_run, runner):
        transient = subprocess.CompletedProcess(
            args=["oc", "get", "nodes"], returncode=1,
            stdout="", stderr="connection refused",
        )
        mock_run.return_value = transient

        result = runner.oc("get", "nodes", retries=0)

        assert result.returncode == 1
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_success_on_first_try_no_retry(self, mock_run, runner):
        success = subprocess.CompletedProcess(
            args=["oc", "get", "nodes"], returncode=0,
            stdout="node1 Ready", stderr="",
        )
        mock_run.return_value = success

        result = runner.oc("get", "nodes")

        assert result.returncode == 0
        assert mock_run.call_count == 1

    @patch("accelerator_ci.shared.oc_runner.time.sleep")
    @patch("subprocess.run")
    def test_exponential_backoff_delays(self, mock_run, mock_sleep, runner):
        transient = subprocess.CompletedProcess(
            args=["oc", "get", "nodes"], returncode=1,
            stdout="", stderr="connection refused",
        )
        mock_run.return_value = transient

        runner.oc("get", "nodes", retries=3)

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1, 2, 4]

    @patch("subprocess.run")
    def test_negative_retries_treated_as_zero(self, mock_run, runner):
        error = subprocess.CompletedProcess(
            args=["oc", "get", "nodes"], returncode=1,
            stdout="", stderr="connection refused",
        )
        mock_run.return_value = error

        result = runner.oc("get", "nodes", retries=-1)

        assert result.returncode == 1
        assert mock_run.call_count == 1
