"""Tests for accelerator_ci.cluster_provision.vm."""

from unittest.mock import patch
import subprocess
import pytest

from accelerator_ci.cluster_provision.vm import (
    vm_state, vm_exists, destroy_vm, shutdown_vm, start_vm,
    shutdown_vms, start_vms, attach_pci_devices, detach_all_pci_devices,
)


def _cp(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class TestVmState:
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_running(self, mock_ssh):
        mock_ssh.return_value = _cp(stdout="running\n")
        assert vm_state("h", "u", "vm1") == "running"

    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_not_found(self, mock_ssh):
        mock_ssh.return_value = _cp(rc=1)
        assert vm_state("h", "u", "vm1") is None


class TestVmExists:
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_exists(self, mock_ssh):
        mock_ssh.return_value = _cp(stdout="running\n")
        assert vm_exists("h", "u", "vm1") is True

    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_not_exists(self, mock_ssh):
        mock_ssh.return_value = _cp(rc=1)
        assert vm_exists("h", "u", "vm1") is False


class TestDestroyVm:
    @patch("accelerator_ci.cluster_provision.vm.time.sleep")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_running_vm_gets_destroyed(self, mock_ssh, mock_sleep):
        mock_ssh.side_effect = [_cp(stdout="running\n"), _cp()]
        destroy_vm("h", "u", "vm1")
        assert mock_ssh.call_count == 2
        assert "destroy" in mock_ssh.call_args_list[1][0][2]

    @patch("accelerator_ci.cluster_provision.vm.time.sleep")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_shut_off_is_noop(self, mock_ssh, mock_sleep):
        mock_ssh.return_value = _cp(stdout="shut off\n")
        destroy_vm("h", "u", "vm1")
        assert mock_ssh.call_count == 1


class TestShutdownVm:
    @patch("accelerator_ci.cluster_provision.vm.time.sleep")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_already_off(self, mock_ssh, mock_sleep):
        mock_ssh.return_value = _cp(stdout="shut off\n")
        shutdown_vm("h", "u", "vm1")
        mock_sleep.assert_not_called()

    @patch("accelerator_ci.cluster_provision.vm.time.sleep")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_shuts_down_gracefully(self, mock_ssh, mock_sleep):
        mock_ssh.side_effect = [
            _cp(stdout="running\n"),  # initial state check
            _cp(),                    # virsh shutdown
            _cp(stdout="shut off\n"), # poll result
        ]
        shutdown_vm("h", "u", "vm1", timeout=10)
        assert "shutdown" in mock_ssh.call_args_list[1][0][2]


class TestStartVm:
    @patch("accelerator_ci.cluster_provision.vm.time.sleep")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_starts_ok(self, mock_ssh, mock_sleep):
        mock_ssh.side_effect = [
            _cp(),                     # virsh start
            _cp(stdout="running\n"),   # poll
        ]
        start_vm("h", "u", "vm1")

    @patch("accelerator_ci.cluster_provision.vm.time.sleep")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_start_timeout(self, mock_ssh, mock_sleep):
        mock_ssh.side_effect = [_cp()] + [_cp(stdout="shut off\n")] * 12
        with pytest.raises(RuntimeError, match="failed to start"):
            start_vm("h", "u", "vm1")


class TestShutdownVms:
    @patch("accelerator_ci.cluster_provision.vm.shutdown_vm")
    def test_calls_for_each_ctlplane(self, mock_shutdown):
        shutdown_vms("h", "u", "mycluster", 3)
        assert mock_shutdown.call_count == 3
        mock_shutdown.assert_any_call("h", "u", "mycluster-ctlplane-0")
        mock_shutdown.assert_any_call("h", "u", "mycluster-ctlplane-1")
        mock_shutdown.assert_any_call("h", "u", "mycluster-ctlplane-2")


class TestStartVms:
    @patch("accelerator_ci.cluster_provision.vm.start_vm")
    def test_calls_for_each_ctlplane(self, mock_start):
        start_vms("h", "u", "mycluster", 2)
        assert mock_start.call_count == 2


class TestAttachPciDevices:
    @patch("accelerator_ci.cluster_provision.vm.start_vm")
    @patch("accelerator_ci.cluster_provision.vm.shutdown_vm")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_attaches_and_starts(self, mock_ssh, mock_shutdown, mock_start):
        mock_ssh.side_effect = [
            _cp(stdout="running\n"),   # vm_state (exists + running)
            _cp(),                     # write xml
            _cp(),                     # virsh attach-device
            _cp(),                     # rm xml
        ]
        attach_pci_devices("h", "u", "vm1", ["0000:06:00.0"])
        mock_shutdown.assert_called_once()
        mock_start.assert_called_once()

    @patch("accelerator_ci.cluster_provision.vm.start_vm")
    @patch("accelerator_ci.cluster_provision.vm.shutdown_vm")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_invalid_pci_address(self, mock_ssh, mock_shutdown, mock_start):
        mock_ssh.return_value = _cp(stdout="shut off\n")  # vm_state
        with pytest.raises(RuntimeError, match="Invalid PCI address"):
            attach_pci_devices("h", "u", "vm1", ["bad"])

    @patch("accelerator_ci.cluster_provision.vm.start_vm")
    @patch("accelerator_ci.cluster_provision.vm.shutdown_vm")
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_vm_not_found(self, mock_ssh, mock_shutdown, mock_start):
        mock_ssh.return_value = _cp(rc=1)  # vm_state returns None
        with pytest.raises(RuntimeError, match="not found"):
            attach_pci_devices("h", "u", "vm1", ["0000:06:00.0"])


class TestDetachAllPciDevices:
    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_no_hostdevs(self, mock_ssh):
        mock_ssh.side_effect = [
            _cp(stdout="shut off\n"),  # vm_state
            _cp(stdout="<domain>no hostdev here</domain>"),  # dumpxml
        ]
        detach_all_pci_devices("h", "u", "vm1")
        assert mock_ssh.call_count == 2

    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_vm_running_raises(self, mock_ssh):
        mock_ssh.return_value = _cp(stdout="running\n")
        with pytest.raises(RuntimeError, match="must be shut off"):
            detach_all_pci_devices("h", "u", "vm1")

    @patch("accelerator_ci.cluster_provision.vm.ssh_cmd")
    def test_detaches_hostdevs(self, mock_ssh):
        xml = (
            "<domain>"
            "<hostdev mode='subsystem' type='pci'>"
            "<source><address domain='0x0000' bus='0x06' slot='0x00' function='0x0'/>"
            "</source></hostdev>"
            "</domain>"
        )
        mock_ssh.side_effect = [
            _cp(stdout="shut off\n"),  # vm_state
            _cp(stdout=xml),           # dumpxml
            _cp(),                     # write xml
            _cp(),                     # detach-device
            _cp(),                     # rm
        ]
        detach_all_pci_devices("h", "u", "vm1")
        assert mock_ssh.call_count == 5
