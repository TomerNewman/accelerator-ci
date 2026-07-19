"""Tests for accelerator_ci.cluster_provision.snapshot."""

from unittest.mock import patch
import subprocess
import pytest

from accelerator_ci.cluster_provision.snapshot import (
    get_snapshot_name, snapshot_cluster_name, find_snapshot,
    create_snapshot, revert_snapshot, delete_snapshot,
    list_cached_clusters, stop_running_clusters,
)


def _cp(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class TestSnapshotClusterName:
    def test_major_minor(self):
        assert snapshot_cluster_name("ocp", "4.22") == "ocp-422"

    def test_major_minor_patch(self):
        assert snapshot_cluster_name("ocp", "4.22.5") == "ocp-422"

    def test_invalid_version(self):
        with pytest.raises(ValueError, match="at least major.minor"):
            snapshot_cluster_name("ocp", "4")


class TestGetSnapshotName:
    def test_full_version(self):
        assert get_snapshot_name("4.22.5") == "ocp-4.22.5"

    def test_major_minor(self):
        assert get_snapshot_name("4.22") == "ocp-4.22"


class TestFindSnapshot:
    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    def test_found(self, mock_ssh):
        mock_ssh.return_value = _cp(rc=0)
        assert find_snapshot("h", "u", "vm1", "4.22") is True

    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    def test_not_found(self, mock_ssh):
        mock_ssh.return_value = _cp(rc=1)
        assert find_snapshot("h", "u", "vm1", "4.22") is False


class TestCreateSnapshot:
    @patch("accelerator_ci.cluster_provision.snapshot.scp_cmd")
    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    @patch("accelerator_ci.cluster_provision.snapshot.vm_state")
    def test_creates_snapshot(self, mock_state, mock_ssh, mock_scp):
        mock_state.return_value = "shut off"
        mock_ssh.side_effect = [
            _cp(rc=1),  # find_snapshot -> not found
            _cp(rc=0),  # virsh snapshot-create-as
            _cp(),      # mkdir
        ]
        result = create_snapshot("h", "u", "vm1", "4.22", "/tmp/kc")
        assert result == "ocp-4.22"
        mock_scp.assert_called_once()

    @patch("accelerator_ci.cluster_provision.snapshot.vm_state")
    def test_vm_not_shut_off(self, mock_state):
        mock_state.return_value = "running"
        with pytest.raises(RuntimeError, match="must be shut off"):
            create_snapshot("h", "u", "vm1", "4.22", "/tmp/kc")

    @patch("accelerator_ci.cluster_provision.snapshot.scp_cmd")
    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    @patch("accelerator_ci.cluster_provision.snapshot.vm_state")
    def test_kubeconfig_save_fails_rolls_back(self, mock_state, mock_ssh, mock_scp):
        mock_state.return_value = "shut off"
        mock_ssh.side_effect = [
            _cp(rc=1),  # find_snapshot -> not found
            _cp(rc=0),  # virsh snapshot-create-as
            _cp(),      # mkdir
            _cp(),      # delete_snapshot -> virsh snapshot-delete
            _cp(),      # delete_snapshot -> rm kubeconfig
        ]
        mock_scp.side_effect = RuntimeError("scp failed")
        with pytest.raises(RuntimeError, match="rolled back"):
            create_snapshot("h", "u", "vm1", "4.22", "/tmp/kc")


class TestRevertSnapshot:
    @patch("accelerator_ci.cluster_provision.snapshot.scp_cmd")
    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    @patch("accelerator_ci.cluster_provision.snapshot.shutdown_vm")
    @patch("accelerator_ci.cluster_provision.snapshot.vm_state")
    def test_reverts_running_vm(self, mock_state, mock_shutdown, mock_ssh, mock_scp):
        mock_state.return_value = "running"
        mock_ssh.side_effect = [
            _cp(rc=0),  # find_snapshot -> found
            _cp(rc=0),  # virsh snapshot-revert
        ]
        revert_snapshot("h", "u", "vm1", "4.22", "/tmp/kc")
        mock_shutdown.assert_called_once()
        mock_scp.assert_called_once()

    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    @patch("accelerator_ci.cluster_provision.snapshot.shutdown_vm")
    @patch("accelerator_ci.cluster_provision.snapshot.vm_state")
    def test_snapshot_not_found(self, mock_state, mock_shutdown, mock_ssh):
        mock_state.return_value = "shut off"
        mock_ssh.return_value = _cp(rc=1)  # find_snapshot -> not found
        with pytest.raises(RuntimeError, match="No snapshot"):
            revert_snapshot("h", "u", "vm1", "4.22", "/tmp/kc")


class TestDeleteSnapshot:
    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    def test_deletes(self, mock_ssh):
        mock_ssh.side_effect = [_cp(), _cp()]
        delete_snapshot("h", "u", "vm1", "4.22")
        assert "snapshot-delete" in mock_ssh.call_args_list[0][0][2]
        assert "rm -f" in mock_ssh.call_args_list[1][0][2]

    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    def test_not_found_is_ok(self, mock_ssh):
        mock_ssh.return_value = _cp(rc=1, stderr="snapshot not found")
        delete_snapshot("h", "u", "vm1", "4.22")


class TestListCachedClusters:
    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    def test_lists_clusters(self, mock_ssh):
        mock_ssh.return_value = _cp(stdout=(
            "ocp-420-ctlplane-0\n"
            "ocp-421-ctlplane-0\n"
            "ocp-422-ctlplane-0\n"
            "ocp-ctlplane-0\n"  # legacy, ignored
            "\n"
        ))
        result = list_cached_clusters("h", "u", "ocp")
        assert result == ["ocp-420", "ocp-421", "ocp-422"]

    @patch("accelerator_ci.cluster_provision.snapshot.ssh_cmd")
    def test_empty_host(self, mock_ssh):
        mock_ssh.return_value = _cp(rc=1)
        assert list_cached_clusters("h", "u", "ocp") == []


class TestStopRunningClusters:
    @patch("accelerator_ci.cluster_provision.snapshot.shutdown_vm")
    @patch("accelerator_ci.cluster_provision.snapshot.vm_state")
    @patch("accelerator_ci.cluster_provision.snapshot.list_cached_clusters")
    def test_stops_running_skips_excluded(self, mock_list, mock_state, mock_shutdown):
        mock_list.return_value = ["ocp-420", "ocp-421"]
        mock_state.side_effect = lambda h, u, vm: "running" if "420" in vm else "shut off"
        stop_running_clusters("h", "u", "ocp", exclude="ocp-421")
        mock_shutdown.assert_called_once_with("h", "u", "ocp-420-ctlplane-0")
