from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from accelerator_ci.cluster_provision.delete import delete_cluster


class TestDeleteCluster:
    @patch("accelerator_ci.cluster_provision.delete._delete_local")
    @patch("accelerator_ci.cluster_provision.delete.ensure_kcli_installed")
    def test_local_dispatch(self, kcli, local):
        delete_cluster({"cluster": "ocp"})
        local.assert_called_once_with("ocp")

    @patch("accelerator_ci.cluster_provision.delete._delete_remote")
    @patch("accelerator_ci.cluster_provision.delete.ensure_kcli_installed")
    def test_remote_dispatch(self, kcli, remote):
        delete_cluster({"cluster": "ocp"}, remote_host="host", remote_user="root")
        remote.assert_called_once_with("ocp", "host", "root", None)

    @patch("accelerator_ci.cluster_provision.delete._delete_local")
    @patch("accelerator_ci.cluster_provision.delete.ensure_kcli_installed")
    def test_default_cluster_name(self, kcli, local):
        delete_cluster({})
        local.assert_called_once_with("ocp")


class TestDeleteLocal:
    @patch("accelerator_ci.cluster_provision.delete.shutil.rmtree")
    @patch("accelerator_ci.cluster_provision.delete.run")
    def test_deletes_and_cleans_up(self, mock_run, mock_rmtree, tmp_path):
        with patch("accelerator_ci.cluster_provision.delete.Path.home", return_value=tmp_path):
            cluster_dir = tmp_path / ".kcli" / "clusters" / "test"
            cluster_dir.mkdir(parents=True)

            from accelerator_ci.cluster_provision.delete import _delete_local
            _delete_local("test")

            cmd = mock_run.call_args[0][0]
            assert cmd == ["kcli", "delete", "cluster", "test", "--yes"]
            mock_rmtree.assert_called_once()


class TestDeleteRemote:
    @patch("accelerator_ci.cluster_provision.delete.run")
    def test_runs_kcli_delete(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with patch("accelerator_ci.cluster_provision.delete.Path.home") as mock_home:
            mock_home.return_value = Path("/tmp/fake-home")
            from accelerator_ci.cluster_provision.delete import _delete_remote

            with patch("accelerator_ci.cluster_provision.remote.check_ssh_connectivity", return_value=(True, "")):
                with patch("accelerator_ci.cluster_provision.remote.get_kcli_client_name", return_value="gpu01"):
                    _delete_remote("ocp", "gpu01.lab.local", "root")

        delete_calls = [c for c in mock_run.call_args_list if "delete" in str(c)]
        assert len(delete_calls) >= 1
        delete_cmd = delete_calls[0][0][0]
        assert "ocp" in delete_cmd
        assert "--yes" in delete_cmd
