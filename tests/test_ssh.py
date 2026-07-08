from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

import accelerator_ci.shared.ssh as ssh_mod
from accelerator_ci.shared.ssh import (
    set_ssh_key_path,
    ssh_cmd,
    scp_cmd,
    close_ssh_multiplexing,
    get_ssh_opts,
)


@pytest.fixture(autouse=True)
def _reset_key():
    original = ssh_mod.ssh_key_path
    yield
    ssh_mod.ssh_key_path = original


class TestSetSshKeyPath:
    def test_valid_key(self, tmp_path):
        key = tmp_path / "id_rsa"
        key.write_text("private-key")
        key.chmod(0o600)
        set_ssh_key_path(str(key))
        assert ssh_mod.ssh_key_path == str(key)

    def test_fixes_permissions(self, tmp_path):
        key = tmp_path / "id_rsa"
        key.write_text("private-key")
        key.chmod(0o644)
        set_ssh_key_path(str(key))
        assert key.stat().st_mode & 0o777 == 0o600

    def test_missing_key_raises(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            set_ssh_key_path("/no/such/key")

    def test_none_clears(self):
        ssh_mod.ssh_key_path = "/old"
        set_ssh_key_path(None)
        assert ssh_mod.ssh_key_path is None


class TestGetSshOpts:
    def test_includes_base_opts(self):
        ssh_mod.ssh_key_path = None
        opts = get_ssh_opts()
        assert "StrictHostKeyChecking=no" in opts

    def test_includes_key_when_set(self, tmp_path):
        key = tmp_path / "id_rsa"
        key.write_text("k")
        key.chmod(0o600)
        set_ssh_key_path(str(key))
        opts = get_ssh_opts()
        assert str(key) in opts


class TestSshCmd:
    @patch("accelerator_ci.shared.ssh.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(["ssh"], 0, stdout="hi", stderr="")
        result = ssh_cmd("host", "root", "echo hi")
        assert result.returncode == 0
        args = mock_run.call_args[0][0]
        assert args[0] == "ssh"
        assert "root@host" in args

    @patch("accelerator_ci.shared.ssh.subprocess.run")
    def test_timeout_with_check_raises(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["ssh"], 10)
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            ssh_cmd("host", "root", "sleep 999", check=True, timeout=10)
        assert exc_info.value.returncode == 124

    @patch("accelerator_ci.shared.ssh.subprocess.run")
    def test_timeout_without_check(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["ssh"], 10)
        result = ssh_cmd("host", "root", "sleep 999", check=False, timeout=10)
        assert result.returncode == 1
        assert "timed out" in result.stderr


class TestScpCmd:
    @patch("accelerator_ci.shared.ssh.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(["scp"], 0, stdout="", stderr="")
        result = scp_cmd("/local/file", "root@host:/remote/file")
        assert result.returncode == 0

    @patch("accelerator_ci.shared.ssh.subprocess.run")
    def test_timeout_raises_runtime_error(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["scp"], 60)
        with pytest.raises(RuntimeError, match="timed out"):
            scp_cmd("/a", "root@h:/b")


class TestCloseSshMultiplexing:
    @patch("accelerator_ci.shared.ssh.subprocess.run")
    def test_calls_ssh_exit(self, mock_run):
        close_ssh_multiplexing("host", "root")
        args = mock_run.call_args[0][0]
        assert "-O" in args
        assert "exit" in args
