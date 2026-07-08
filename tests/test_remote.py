from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from accelerator_ci.cluster_provision.common import DeployError
from accelerator_ci.cluster_provision.remote import (
    check_ssh_connectivity,
    get_kcli_client_name,
    attach_pci_devices,
)


class TestCheckSshConnectivity:
    @patch("accelerator_ci.cluster_provision.remote.ssh_cmd")
    def test_success(self, mock_ssh):
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        ok, err = check_ssh_connectivity("host", "root")
        assert ok is True
        assert err == ""

    @patch("accelerator_ci.cluster_provision.remote.ssh_cmd")
    def test_failure(self, mock_ssh):
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="denied")
        ok, err = check_ssh_connectivity("host", "root")
        assert ok is False
        assert "denied" in err

    @patch("accelerator_ci.cluster_provision.remote.ssh_cmd")
    def test_timeout(self, mock_ssh):
        mock_ssh.side_effect = subprocess.TimeoutExpired(["ssh"], 30)
        ok, err = check_ssh_connectivity("host", "root")
        assert ok is False
        assert "timed out" in err

    @patch("accelerator_ci.cluster_provision.remote.ssh_cmd")
    def test_exception(self, mock_ssh):
        mock_ssh.side_effect = OSError("network down")
        ok, err = check_ssh_connectivity("host", "root")
        assert ok is False
        assert "network down" in err


class TestGetKcliClientName:
    def test_fqdn(self):
        assert get_kcli_client_name("gpu-host.lab.example.com") == "gpu-host"

    def test_short_name(self):
        assert get_kcli_client_name("myhost") == "myhost"


class TestAttachPciDevices:
    @patch("accelerator_ci.cluster_provision.remote.time.sleep", lambda _: None)
    @patch("accelerator_ci.cluster_provision.remote.ssh_cmd")
    def test_invalid_pci_address(self, mock_ssh):
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, stdout="running", stderr="")
        with pytest.raises(DeployError, match="Invalid PCI address"):
            attach_pci_devices("host", "root", "vm1", ["bad"])

    @patch("accelerator_ci.cluster_provision.remote.time.sleep", lambda _: None)
    @patch("accelerator_ci.cluster_provision.remote.ssh_cmd")
    def test_vm_not_found(self, mock_ssh):
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="no state")
        with pytest.raises(DeployError, match="not found"):
            attach_pci_devices("host", "root", "vm1", ["0000:03:00.0"])

    @patch("accelerator_ci.cluster_provision.remote.time.sleep", lambda _: None)
    @patch("accelerator_ci.cluster_provision.remote.ssh_cmd")
    def test_non_hex_pci_address(self, mock_ssh):
        def side_effect(*args, **kwargs):
            cmd = args[2] if len(args) > 2 else kwargs.get("command", "")
            if "domstate" in cmd:
                return subprocess.CompletedProcess([], 0, stdout="shut off", stderr="")
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        mock_ssh.side_effect = side_effect
        with pytest.raises(DeployError, match="non-hex"):
            attach_pci_devices("host", "root", "vm1", ["0000:ZZ:00.0"])
