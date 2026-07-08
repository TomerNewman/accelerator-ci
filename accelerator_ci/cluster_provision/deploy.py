"""Deploy OpenShift cluster using kcli (local or remote libvirt)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from accelerator_ci.cluster_provision.common import DeployError, run
from accelerator_ci.cluster_provision.config import get_cluster_topology_description
from accelerator_ci.cluster_provision.kcli_preflight import ensure_kcli_installed, ensure_pull_secret_exists, ensure_kcli_config
from accelerator_ci.shared.progress import ProgressTracker

logger = logging.getLogger(__name__)


def push_ssh_key_to_remote(host: str, user: str) -> None:
    """kcli injects the CI runner's public key into VMs, so the remote host
    needs the matching private key to SSH into its own VMs."""
    from accelerator_ci.shared.ssh import ssh_key_path, scp_cmd, ssh_cmd

    local_key = Path(ssh_key_path) if ssh_key_path else Path.home() / ".ssh" / "id_rsa"
    if not local_key.exists():
        logger.debug("No SSH key to push — skipping.")
        return

    logger.info("Copying SSH key to remote host for VM access")
    scp_cmd(str(local_key), f"{user}@{host}:/root/.ssh/id_rsa")
    ssh_cmd(host, user, "chmod 600 /root/.ssh/id_rsa", check=False)
    ssh_cmd(
        host, user,
        "ssh-keygen -y -f /root/.ssh/id_rsa > /root/.ssh/id_rsa.pub 2>/dev/null",
        check=False,
    )


def fix_vm_container_storage(
    host: str, user: str, cluster_name: str, ctlplanes: int
) -> None:
    """Wipe pre-baked container storage on RHCOS VMs via guestfish.
    Works around a composefs/overlay bug where podman pull fails."""
    from accelerator_ci.shared.ssh import ssh_cmd

    logger.info("Fixing RHCOS container storage (composefs overlay workaround)...")

    r = ssh_cmd(host, user, "command -v guestfish", check=False)
    if r.returncode != 0:
        logger.info("Installing libguestfs-tools on remote host...")
        ssh_cmd(host, user, "dnf -y install libguestfs-tools-c", check=False, timeout=300)

    storage_path = "/ostree/deploy/rhcos/var/lib/containers/storage"

    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"

        r = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
        if "shut off" not in (r.stdout or ""):
            logger.warning("%s: VM is not shut off — skipping.", vm_name)
            continue

        gf_script = f"run\nmount /dev/sda4 /\nglob rm-rf {storage_path}/*\n"
        r = ssh_cmd(host, user, f"echo '{gf_script}' | guestfish --rw -d {vm_name}", check=False, timeout=120)
        if r.returncode == 0:
            logger.info("%s: container storage wiped.", vm_name)
        else:
            logger.warning("%s: guestfish failed (rc=%d): %s",
                           vm_name, r.returncode, (r.stderr or '').strip())


def shutdown_vms(host: str, user: str, cluster_name: str, ctlplanes: int) -> None:
    from accelerator_ci.shared.ssh import ssh_cmd

    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"
        ssh_cmd(host, user, f"virsh shutdown {vm_name}", check=False)

    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"
        for _ in range(24):
            time.sleep(5)
            r = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
            if "shut off" in (r.stdout or ""):
                logger.info("%s shut off.", vm_name)
                break
        else:
            ssh_cmd(host, user, f"virsh destroy {vm_name}", check=False)
            time.sleep(2)


def start_vms(host: str, user: str, cluster_name: str, ctlplanes: int) -> None:
    from accelerator_ci.shared.ssh import ssh_cmd

    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"
        ssh_cmd(host, user, f"virsh start {vm_name}", check=False)
        logger.info("%s started.", vm_name)


def build_kcli_params(params: dict[str, str]) -> list[str]:
    args = []
    for key, value in params.items():
        args.extend(["-P", f"{key}={value}"])
    return args


def deploy_cluster(
    params: dict[str, Any],
    remote_host: str | None,
    remote_user: str,
    wait_timeout: int,
    ssh_key: str | None,
    pci_devices: list[str] | None,
    json_progress: bool = False,
) -> None:
    ensure_kcli_installed()

    cluster_name = params["cluster"]
    api_ip = params["api_ip"]
    domain = params["domain"]
    ctlplanes = int(params["ctlplanes"])
    workers = int(params["workers"])

    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        logger.info("Removing existing kcli cluster artifacts directory: %s", clusters_dir)
        shutil.rmtree(clusters_dir)

    pull_secret_path_str = params.get("pull_secret", "")
    if not pull_secret_path_str:
        raise DeployError("Missing 'pull_secret' in parameters.")
    pull_secret_path = Path(pull_secret_path_str)

    ensure_pull_secret_exists(pull_secret_path)

    if remote_host:
        deploy_remote(
            params=params,
            cluster_name=cluster_name,
            api_ip=api_ip,
            domain=domain,
            ctlplanes=ctlplanes,
            workers=workers,
            remote_host=remote_host,
            remote_user=remote_user,
            wait_timeout=wait_timeout,
            ssh_key=ssh_key,
            pci_devices=pci_devices,
            json_progress=json_progress,
        )
    else:
        deploy_local(
            params=params,
            ctlplanes=ctlplanes,
            workers=workers,
            json_progress=json_progress,
        )


def deploy_local(
    params: dict[str, Any],
    ctlplanes: int,
    workers: int,
    json_progress: bool = False,
) -> None:
    ensure_kcli_config()

    topology = get_cluster_topology_description(ctlplanes, workers)

    progress = ProgressTracker("deploy", [
        f"Run kcli create cluster [{topology}]",
    ], json_output=json_progress)
    progress.start()

    try:
        progress.step(1)
        kcli_cmd = ["kcli", "create", "cluster", "openshift"]
        kcli_cmd.extend(build_kcli_params(params))
        logger.debug("kcli command: %s", ' '.join(kcli_cmd))
        run(kcli_cmd, check=True)
        progress.done()
    except Exception as exc:
        progress.fail(str(exc))
        raise


def deploy_remote(
    params: dict[str, Any],
    cluster_name: str,
    api_ip: str,
    domain: str,
    ctlplanes: int,
    workers: int,
    remote_host: str,
    remote_user: str,
    wait_timeout: int,
    ssh_key: str | None = None,
    pci_devices: list[str] | None = None,
    json_progress: bool = False,
) -> None:
    from accelerator_ci.cluster_provision.remote import (
        setup_remote_libvirt,
        configure_kcli_remote_client,
        setup_remote_cluster_access,
        wait_for_cluster_ready,
        get_cluster_status,
        print_access_instructions,
        set_ssh_key_path,
        attach_pci_devices,
    )

    topology = get_cluster_topology_description(ctlplanes, workers)

    if ssh_key:
        set_ssh_key_path(ssh_key)
        logger.info("Using SSH key: %s", ssh_key)

    header_lines = [
        "=" * 60,
        f"Remote OpenShift Deployment [{topology}]",
        "=" * 60,
        f"Remote Host: {remote_user}@{remote_host}",
        f"Cluster Name: {cluster_name}",
        f"Topology: {topology}",
        f"API IP: {api_ip}",
        f"Domain: {domain}",
        f"Wait Timeout: {wait_timeout}s",
    ]
    if ssh_key:
        header_lines.append(f"SSH Key: {ssh_key}")
    header_lines.append("=" * 60)
    logger.info("%s", "\n".join(header_lines))

    progress = ProgressTracker("deploy", [
        "Setup remote host",
        "Configure kcli client",
        "Clean up existing cluster",
        "Deploy OpenShift cluster",
        "Wait for VMs",
        "Fix container storage and attach devices",
        "Setup remote cluster access",
        "Wait for cluster ready",
    ], json_output=json_progress)
    progress.start()

    progress.step(1)
    setup_remote_libvirt(remote_host, remote_user)

    progress.step(2)
    kcli_client = configure_kcli_remote_client(remote_host, remote_user)

    progress.step(3)
    run(["kcli", "-C", kcli_client, "delete", "cluster", cluster_name, "--yes"], check=False)
    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        shutil.rmtree(clusters_dir)

    progress.step(4)
    kcli_cmd = ["kcli", "-C", kcli_client, "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))
    logger.debug("kcli command: %s", ' '.join(kcli_cmd))

    kcli_log = tempfile.NamedTemporaryFile(mode="w", prefix="kcli-", suffix=".log", delete=False)
    kcli_log_path = Path(kcli_log.name)
    kcli_process = subprocess.Popen(
        kcli_cmd,
        stdout=kcli_log,
        stderr=subprocess.STDOUT,
    )

    deploy_failed: Exception | None = None
    try:
        expected_vms = ctlplanes + workers + 1
        min_vms_to_proceed = 2

        progress.step(5)
        vm_wait_timeout = 600
        vm_wait_start = time.time()
        while True:
            if kcli_process.poll() is not None:
                if kcli_process.returncode != 0:
                    kcli_log.flush()
                    logger.error("kcli output:\n%s", kcli_log_path.read_text())
                    raise DeployError(f"kcli deployment failed with exit code {kcli_process.returncode}")

            result = run(["kcli", "-C", kcli_client, "list", "vm"], check=False, capture_output=True)
            vm_count = result.stdout.count(f"{cluster_name}-")

            if vm_count >= min_vms_to_proceed:
                logger.info("VMs deployed: %d found (expecting %d total)", vm_count, expected_vms)
                break

            elapsed = int(time.time() - vm_wait_start)
            if elapsed >= vm_wait_timeout:
                kcli_process.terminate()
                try:
                    kcli_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    kcli_process.kill()
                kcli_log.flush()
                logger.error("kcli output:\n%s", kcli_log_path.read_text())
                raise DeployError("Timeout waiting for VMs to be deployed (10 minutes)")

            if elapsed % 30 == 0 or vm_count > 0:
                logger.info("Waiting for VMs... (%ds elapsed, found %d, expecting %d)", elapsed, vm_count, expected_vms)
            time.sleep(10)

        run(["kcli", "-C", kcli_client, "list", "vm"], check=False)
        push_ssh_key_to_remote(remote_host, remote_user)

        progress.step(6)
        if pci_devices:
            ctlplane_vm = f"{cluster_name}-ctlplane-0"
            attach_pci_devices(
                remote_host, remote_user, ctlplane_vm, pci_devices,
                pre_start_hook=lambda: fix_vm_container_storage(
                    remote_host, remote_user, cluster_name, ctlplanes),
            )
        else:
            shutdown_vms(remote_host, remote_user, cluster_name, ctlplanes)
            fix_vm_container_storage(remote_host, remote_user, cluster_name, ctlplanes)
            start_vms(remote_host, remote_user, cluster_name, ctlplanes)

        progress.step(7)
        setup_remote_cluster_access(remote_host, remote_user, cluster_name, api_ip, domain)

        kcli_process.terminate()
        try:
            kcli_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            kcli_process.kill()

        progress.step(8)
        wait_for_cluster_ready(remote_host, remote_user, api_ip, wait_timeout)

        status = get_cluster_status(remote_host, remote_user)
        logger.info("%s", status)

        print_access_instructions(
            host=remote_host,
            user=remote_user,
            cluster_name=cluster_name,
            api_ip=api_ip,
            domain=domain,
            kcli_client=kcli_client,
        )

        progress.done()

    except Exception as exc:
        deploy_failed = exc
        progress.fail(str(exc))

    finally:
        if kcli_process.poll() is None:
            kcli_process.terminate()
            try:
                kcli_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kcli_process.kill()
        kcli_log.close()
        kcli_log_path.unlink(missing_ok=True)

    if deploy_failed is not None:
        raise deploy_failed
