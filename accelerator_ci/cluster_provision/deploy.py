"""Deploy OpenShift cluster using kcli (local or remote libvirt)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from accelerator_ci.cluster_provision.common import DeployError, run
from accelerator_ci.cluster_provision.config import get_cluster_topology_description
from accelerator_ci.cluster_provision.kcli_preflight import ensure_kcli_installed, ensure_pull_secret_exists, ensure_kcli_config


def push_ssh_key_to_remote(host: str, user: str) -> None:
    """kcli injects the CI runner's public key into VMs, so the remote host
    needs the matching private key to SSH into its own VMs."""
    from accelerator_ci.shared.ssh import ssh_key_path, scp_cmd, ssh_cmd

    local_key = Path(ssh_key_path) if ssh_key_path else Path.home() / ".ssh" / "id_rsa"
    if not local_key.exists():
        print("  No SSH key to push — skipping.")
        return

    print("Copying SSH key to remote host for VM access")
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

    print("\nStep 5c: Fixing RHCOS container storage (composefs overlay workaround)...")

    r = ssh_cmd(host, user, "command -v guestfish", check=False)
    if r.returncode != 0:
        print("  Installing libguestfs-tools on remote host...")
        ssh_cmd(host, user, "dnf -y install libguestfs-tools-c", check=False, timeout=300)

    storage_path = "/ostree/deploy/rhcos/var/lib/containers/storage"

    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"

        r = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
        if "shut off" not in (r.stdout or ""):
            print(f"{vm_name}: VM is not shut off — skipping.")
            continue

        gf_script = f"run\nmount /dev/sda4 /\nglob rm-rf {storage_path}/*\n"
        r = ssh_cmd(host, user, f"echo '{gf_script}' | guestfish --rw -d {vm_name}", check=False, timeout=120)
        if r.returncode == 0:
            print(f"{vm_name}: container storage wiped.")
        else:
            print(f"    {vm_name}: guestfish failed (rc={r.returncode}): "
                  f"{(r.stderr or '').strip()}")


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
                print(f"  {vm_name} shut off.")
                break
        else:
            ssh_cmd(host, user, f"virsh destroy {vm_name}", check=False)
            time.sleep(2)


def start_vms(host: str, user: str, cluster_name: str, ctlplanes: int) -> None:
    from accelerator_ci.shared.ssh import ssh_cmd

    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"
        ssh_cmd(host, user, f"virsh start {vm_name}", check=False)
        print(f"  {vm_name} started.")


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
) -> None:
    ensure_kcli_installed()

    cluster_name = params["cluster"]
    api_ip = params["api_ip"]
    domain = params["domain"]
    ctlplanes = int(params["ctlplanes"])
    workers = int(params["workers"])

    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        print(f"Removing existing kcli cluster artifacts directory: {clusters_dir}")
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
        )
    else:
        deploy_local(
            params=params,
            ctlplanes=ctlplanes,
            workers=workers,
        )


def deploy_local(
    params: dict[str, Any],
    ctlplanes: int,
    workers: int,
) -> None:
    ensure_kcli_config()

    topology = get_cluster_topology_description(ctlplanes, workers)

    kcli_cmd = ["kcli", "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))

    print(f"\nStarting OpenShift deployment [{topology}] with kcli...")
    print(f"  kcli command: {' '.join(kcli_cmd)}")
    run(kcli_cmd, check=True)
    print(f"\nOpenShift deployment [{topology}] command has completed.")
    print("Check 'kcli list' and the OpenShift console once the cluster is fully up.")


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
        print(f"Using SSH key: {ssh_key}")

    print(f"\n{'='*60}")
    print(f"Remote OpenShift Deployment [{topology}]")
    print(f"{'='*60}")
    print(f"Remote Host: {remote_user}@{remote_host}")
    print(f"Cluster Name: {cluster_name}")
    print(f"Topology: {topology}")
    print(f"API IP: {api_ip}")
    print(f"Domain: {domain}")
    print(f"Wait Timeout: {wait_timeout}s")
    if ssh_key:
        print(f"SSH Key: {ssh_key}")
    print(f"{'='*60}\n")

    print("Step 1: Setting up remote host...")
    setup_remote_libvirt(remote_host, remote_user)

    print("\nStep 2: Configuring kcli client...")
    kcli_client = configure_kcli_remote_client(remote_host, remote_user)

    print(f"\nStep 3: Cleaning up any existing cluster '{cluster_name}'...")
    run(["kcli", "-C", kcli_client, "delete", "cluster", cluster_name, "--yes"], check=False)
    clusters_dir = Path.home() / ".kcli" / "clusters" / cluster_name
    if clusters_dir.is_dir():
        shutil.rmtree(clusters_dir)

    print(f"\nStep 4: Deploying OpenShift cluster [{topology}]...")
    print("Starting kcli deployment (monitoring will be done via remote host)...")

    kcli_cmd = ["kcli", "-C", kcli_client, "create", "cluster", "openshift"]
    kcli_cmd.extend(build_kcli_params(params))

    print(f"  kcli command: {' '.join(kcli_cmd)}")
    print("\n  Starting kcli in background...")

    kcli_log = tempfile.NamedTemporaryFile(mode="w", prefix="kcli-", suffix=".log", delete=False)
    kcli_log_path = Path(kcli_log.name)
    kcli_process = subprocess.Popen(
        kcli_cmd,
        stdout=kcli_log,
        stderr=subprocess.STDOUT,
    )

    try:
        expected_vms = ctlplanes + workers + 1
        min_vms_to_proceed = 2

        print("\nStep 5: Waiting for VMs to be deployed...")
        vm_wait_timeout = 600
        vm_wait_start = time.time()
        while True:
            if kcli_process.poll() is not None:
                if kcli_process.returncode != 0:
                    print(f"\nkcli process exited with code {kcli_process.returncode}")
                    print("kcli output:")
                    kcli_log.flush()
                    print(kcli_log_path.read_text())
                    raise DeployError(f"kcli deployment failed with exit code {kcli_process.returncode}")

            result = run(["kcli", "-C", kcli_client, "list", "vm"], check=False, capture_output=True)
            vm_count = result.stdout.count(f"{cluster_name}-")

            if vm_count >= min_vms_to_proceed:
                print(f"  VMs deployed: {vm_count} VMs found (expecting {expected_vms} total)")
                break

            elapsed = int(time.time() - vm_wait_start)
            if elapsed >= vm_wait_timeout:
                kcli_process.terminate()
                try:
                    kcli_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    kcli_process.kill()
                kcli_log.flush()
                print("\nkcli output:")
                print(kcli_log_path.read_text())
                raise DeployError("Timeout waiting for VMs to be deployed (10 minutes)")

            if elapsed % 30 == 0 or vm_count > 0:
                print(f"  Waiting for VMs... ({elapsed}s elapsed, found {vm_count} VMs, expecting {expected_vms})")
            time.sleep(10)

        print("\nVMs on remote host:")
        run(["kcli", "-C", kcli_client, "list", "vm"], check=False)

        push_ssh_key_to_remote(remote_host, remote_user)

        if pci_devices:
            print("\nStep 5b: Attaching PCI devices to control plane VM...")
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

        print("\nStep 6: Setting up remote cluster access...")
        setup_remote_cluster_access(remote_host, remote_user, cluster_name, api_ip, domain)

        print("\nStopping kcli monitoring (we'll monitor via remote host)...")
        kcli_process.terminate()
        try:
            kcli_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            kcli_process.kill()

        print(f"\nStep 7: Waiting for cluster to be ready...")
        wait_for_cluster_ready(remote_host, remote_user, api_ip, wait_timeout)

        print("\n" + "=" * 60)
        print("CLUSTER STATUS")
        print("=" * 60)
        status = get_cluster_status(remote_host, remote_user)
        print(status)

        print_access_instructions(
            host=remote_host,
            user=remote_user,
            cluster_name=cluster_name,
            api_ip=api_ip,
            domain=domain,
            kcli_client=kcli_client,
        )

        print("\nDeployment completed successfully!")

    finally:
        if kcli_process.poll() is None:
            kcli_process.terminate()
            try:
                kcli_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kcli_process.kill()
        kcli_log.close()
        kcli_log_path.unlink(missing_ok=True)
