"""VM snapshot management for cluster caching.

Manages virsh snapshots on a remote libvirt host so that an OCP cluster
can be restored quickly instead of re-deployed from scratch.

Snapshot naming: ``ocp-<version>`` (e.g. ``ocp-4.22``).
Kubeconfig is saved alongside the snapshot for full restore.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from accelerator_ci.shared.ssh import ssh_cmd, scp_cmd
from accelerator_ci.cluster_provision.vm import vm_state, shutdown_vm, destroy_vm

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = "/var/lib/libvirt/accelerator-ci-snapshots"
SNAPSHOT_PREFIX = "ocp-"


def get_snapshot_name(ocp_version: str) -> str:
    return f"{SNAPSHOT_PREFIX}{ocp_version}"


def snapshot_cluster_name(base_name: str, ocp_version: str) -> str:
    """Version-specific cluster name: ("ocp", "4.22.5") -> "ocp-422"."""
    parts = ocp_version.split(".")
    if len(parts) < 2:
        raise ValueError(f"OCP version must be at least major.minor (got '{ocp_version}')")
    return f"{base_name}-{parts[0]}{parts[1]}"


def find_snapshot(host: str, user: str, vm_name: str, ocp_version: str) -> bool:
    """Check if a snapshot exists for the given OCP version."""
    snap_name = get_snapshot_name(ocp_version)
    r = ssh_cmd(
        host, user,
        f"virsh snapshot-list {vm_name} --name 2>/dev/null | grep -Fqx '{snap_name}'",
        check=False,
    )
    return r.returncode == 0


def create_snapshot(
    host: str,
    user: str,
    vm_name: str,
    ocp_version: str,
    kubeconfig_local_path: str,
) -> str:
    """Create a snapshot of a shut-off VM and save the kubeconfig.

    Returns the snapshot name.
    """
    snap_name = get_snapshot_name(ocp_version)

    state = vm_state(host, user, vm_name)
    if state is None:
        raise RuntimeError(f"VM {vm_name} not found on {host}")
    if state != "shut off":
        raise RuntimeError(
            f"VM {vm_name} must be shut off to create a snapshot (current state: {state})"
        )

    if find_snapshot(host, user, vm_name, ocp_version):
        logger.info("Snapshot '%s' already exists — replacing", snap_name)
        delete_snapshot(host, user, vm_name, ocp_version)

    logger.info("Creating snapshot '%s' for VM '%s'", snap_name, vm_name)
    r = ssh_cmd(
        host, user,
        f"virsh snapshot-create-as {vm_name} --name {snap_name} "
        f"--description 'accelerator-ci cache: OCP {ocp_version}' --atomic",
        check=False,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Failed to create snapshot '{snap_name}': {r.stderr or r.stdout}")

    try:
        ssh_cmd(host, user, f"mkdir -p {SNAPSHOT_DIR}", check=False)
        scp_cmd(
            kubeconfig_local_path,
            f"{user}@{host}:{SNAPSHOT_DIR}/{snap_name}.kubeconfig",
        )
        logger.info("Kubeconfig saved to %s/%s.kubeconfig", SNAPSHOT_DIR, snap_name)
    except Exception as exc:
        logger.error("Failed to save kubeconfig — rolling back snapshot: %s", exc)
        delete_snapshot(host, user, vm_name, ocp_version)
        raise RuntimeError(
            f"Snapshot '{snap_name}' rolled back: kubeconfig save failed"
        ) from exc

    return snap_name


def revert_snapshot(
    host: str,
    user: str,
    vm_name: str,
    ocp_version: str,
    kubeconfig_local_path: str,
) -> None:
    """Revert a VM to a previously saved snapshot and restore the kubeconfig.

    After reverting, the VM will be in shut-off state.
    """
    snap_name = get_snapshot_name(ocp_version)

    if not find_snapshot(host, user, vm_name, ocp_version):
        raise RuntimeError(f"No snapshot '{snap_name}' found for VM '{vm_name}'")

    if vm_state(host, user, vm_name) == "running":
        shutdown_vm(host, user, vm_name)

    logger.info("Reverting VM '%s' to snapshot '%s'", vm_name, snap_name)
    r = ssh_cmd(
        host, user,
        f"virsh snapshot-revert {vm_name} --snapshotname {snap_name}",
        check=False,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Failed to revert to snapshot '{snap_name}': {r.stderr or r.stdout}")

    kubeconfig_dest = Path(kubeconfig_local_path)
    kubeconfig_dest.parent.mkdir(parents=True, exist_ok=True)
    scp_cmd(
        f"{user}@{host}:{SNAPSHOT_DIR}/{snap_name}.kubeconfig",
        str(kubeconfig_dest),
    )
    logger.info("Kubeconfig restored to %s", kubeconfig_dest)


def delete_snapshot(host: str, user: str, vm_name: str, ocp_version: str) -> None:
    """Delete a snapshot and its saved kubeconfig."""
    snap_name = get_snapshot_name(ocp_version)

    r = ssh_cmd(
        host, user,
        f"virsh snapshot-delete {vm_name} --snapshotname {snap_name}",
        check=False,
        timeout=120,
    )
    not_found = "not found" in (r.stderr or "").lower()
    if r.returncode != 0 and not not_found:
        logger.warning("Failed to delete snapshot '%s': %s", snap_name, r.stderr)
        return

    ssh_cmd(host, user, f"rm -f {SNAPSHOT_DIR}/{snap_name}.kubeconfig", check=False)


def list_cached_clusters(host: str, user: str, base_name: str) -> list[str]:
    """List version-specific cluster names on the remote host.

    Returns sorted list like ["ocp-420", "ocp-421", "ocp-422"].
    """
    r = ssh_cmd(host, user, "virsh list --all --name", check=False)
    if r.returncode != 0 or not r.stdout:
        return []

    clusters: set[str] = set()
    prefix = f"{base_name}-"
    for line in r.stdout.strip().splitlines():
        vm = line.strip()
        if vm.startswith(prefix) and "-ctlplane-" in vm:
            cluster = vm.rsplit("-ctlplane-", 1)[0]
            if cluster != base_name:
                clusters.add(cluster)
    return sorted(clusters)


def evict_cached_clusters(
    host: str,
    user: str,
    base_name: str,
    max_cached: int,
    exclude: str | None = None,
) -> None:
    """Delete the oldest cached clusters when count exceeds max_cached."""
    from accelerator_ci.cluster_provision.common import run
    from accelerator_ci.cluster_provision.remote import get_kcli_client_name

    clusters = list_cached_clusters(host, user, base_name)
    if exclude:
        clusters = [c for c in clusters if c != exclude]

    kcli_client = get_kcli_client_name(host)

    while len(clusters) >= max_cached:
        victim = clusters.pop(0)
        logger.info("Evicting cached cluster: %s", victim)
        result = run(["kcli", "-C", kcli_client, "delete", "cluster", victim, "--yes"], check=False)
        if result.returncode != 0:
            logger.warning("Failed to evict %s (rc=%d)", victim, result.returncode)

        local_dir = Path.home() / ".kcli" / "clusters" / victim
        if local_dir.is_dir():
            shutil.rmtree(local_dir)


def stop_running_clusters(
    host: str,
    user: str,
    base_name: str,
    exclude: str | None = None,
) -> None:
    """Shut down any running cached cluster VMs (except exclude)."""
    for cluster in list_cached_clusters(host, user, base_name):
        if cluster == exclude:
            continue
        vm = f"{cluster}-ctlplane-0"
        if vm_state(host, user, vm) == "running":
            logger.info("Stopping running cached cluster %s", cluster)
            shutdown_vm(host, user, vm)
        bootstrap = f"{cluster}-bootstrap"
        if vm_state(host, user, bootstrap) == "running":
            destroy_vm(host, user, bootstrap)
