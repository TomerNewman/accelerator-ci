"""Delete OpenShift cluster using kcli (local or remote libvirt)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from accelerator_ci.cluster_provision.common import run
from accelerator_ci.cluster_provision.kcli_preflight import ensure_kcli_installed


def delete_cluster(
    params: dict[str, Any],
    remote_host: str | None = None,
    remote_user: str = "root",
    ssh_key: str | None = None,
) -> None:
    ensure_kcli_installed()

    cluster_name = params.get("cluster", "ocp")

    print(f"Preparing to delete cluster: {cluster_name}")

    if remote_host:
        _delete_remote(cluster_name, remote_host, remote_user, ssh_key)
    else:
        _delete_local(cluster_name)


def _delete_local(cluster_name: str) -> None:
    print(f"Deleting cluster {cluster_name}...")
    run(["kcli", "delete", "cluster", cluster_name, "--yes"], check=True)

    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        print(f"Removing cluster artifacts directory: {clusters_dir}")
        shutil.rmtree(clusters_dir)

    print(f"Cluster {cluster_name} deleted.")


def _delete_remote(
    cluster_name: str,
    remote_host: str,
    remote_user: str,
    ssh_key: str | None = None,
) -> None:
    from accelerator_ci.cluster_provision.remote import get_kcli_client_name, configure_kcli_remote_client, check_ssh_connectivity, set_ssh_key_path

    if ssh_key:
        set_ssh_key_path(ssh_key)
        print(f"Using SSH key: {ssh_key}")

    print(f"\nDeleting remote cluster: {cluster_name}")
    print(f"Remote host: {remote_user}@{remote_host}")

    ssh_ok, ssh_error = check_ssh_connectivity(remote_host, remote_user)
    if not ssh_ok:
        print(f"WARNING: Cannot connect to {remote_user}@{remote_host} via SSH: {ssh_error}")
        print("Attempting to delete using existing kcli configuration...")

    kcli_client = get_kcli_client_name(remote_host)

    result = run(["kcli", "-C", kcli_client, "list", "vm"], check=False, capture_output=True)
    if result.returncode != 0:
        print(f"Configuring kcli client '{kcli_client}'...")
        kcli_client = configure_kcli_remote_client(remote_host, remote_user)

    print(f"Deleting cluster {cluster_name} from remote host...")
    run(
        ["kcli", "-C", kcli_client, "delete", "cluster", cluster_name, "--yes"],
        check=False,
    )

    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        print(f"Removing cluster artifacts directory: {clusters_dir}")
        shutil.rmtree(clusters_dir)

    print(f"Cluster {cluster_name} deletion complete.")
