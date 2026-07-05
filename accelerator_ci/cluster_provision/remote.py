"""Remote libvirt host management (kcli prerequisites, SSH, PCI passthrough)."""

from __future__ import annotations

import base64
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from accelerator_ci.cluster_provision.common import DeployError, run
import accelerator_ci.shared.ssh as _ssh_mod
from accelerator_ci.shared.ssh import (
    set_ssh_key_path,
    get_ssh_opts,
    ssh_cmd,
    scp_cmd,
)


def check_ssh_connectivity(host: str, user: str) -> tuple[bool, str]:
    try:
        result = ssh_cmd(host, user, "echo 'ok'", check=False)
        if result.returncode == 0 and "ok" in result.stdout:
            return True, ""

        error_msg = f"SSH connection failed (exit code {result.returncode})"
        if result.stderr:
            error_msg += f"\nSTDERR: {result.stderr.strip()}"
        if result.stdout:
            error_msg += f"\nSTDOUT: {result.stdout.strip()}"
        return False, error_msg
    except subprocess.TimeoutExpired as e:
        return False, f"SSH connection timed out after {e.timeout}s"
    except Exception as e:
        return False, f"SSH connection failed: {str(e)}"


def setup_remote_libvirt(host: str, user: str) -> None:
    """Set up libvirt and prerequisites on the remote host (idempotent)."""
    print(f"Setting up remote host: {user}@{host}")

    ssh_success, ssh_error = check_ssh_connectivity(host, user)
    if not ssh_success:
        raise DeployError(f"Cannot SSH to {user}@{host}: {ssh_error}")
    print("  SSH connection verified.")

    result = ssh_cmd(host, user, "command -v virsh", check=False)
    if result.returncode != 0:
        print("  libvirt not found. Installing per kcli prerequisites...")

        dnf_check = ssh_cmd(host, user, "command -v dnf", check=False)
        yum_check = ssh_cmd(host, user, "command -v yum", check=False)
        apt_check = ssh_cmd(host, user, "command -v apt-get", check=False)

        if dnf_check.returncode == 0:
            print("  Using dnf to install libvirt (RHEL/Fedora)...")
            ssh_cmd(host, user, "dnf -y install libvirt libvirt-daemon-driver-qemu qemu-kvm tar")
        elif yum_check.returncode == 0:
            print("  Using yum to install libvirt (CentOS/older RHEL)...")
            ssh_cmd(host, user, "yum -y install libvirt libvirt-daemon-driver-qemu qemu-kvm tar")
        elif apt_check.returncode == 0:
            print("  Using apt-get to install libvirt (Debian/Ubuntu)...")
            ssh_cmd(host, user, "apt-get update && apt-get install -y libvirt-daemon-system libvirt-clients qemu-kvm")
        else:
            raise DeployError("No supported package manager found on remote host (dnf/yum/apt-get)")

        ssh_cmd(host, user, "usermod -aG qemu,libvirt $(id -un)", check=False)
        ssh_cmd(host, user, "systemctl enable --now libvirtd")
        print("  libvirt installed successfully.")
    else:
        print("  libvirt is already installed.")

    print("  Fixing libvirt token permissions...")
    ssh_cmd(host, user, "rm -rf /run/libvirt/common && systemctl restart virtlogd libvirtd", check=False)

    print("  Enabling modular libvirt daemons...")
    ssh_cmd(
        host, user,
        "for svc in virtqemud virtstoraged virtnetworkd virtnodedevd virtsecretd virtinterfaced virtnwfilterd; do "
        "systemctl enable --now ${svc}.socket 2>/dev/null || true; "
        "systemctl enable --now ${svc}-ro.socket 2>/dev/null || true; "
        "systemctl enable --now ${svc}-admin.socket 2>/dev/null || true; "
        "done",
        check=False
    )

    print("  Checking/creating default storage pool...")
    pool_check = ssh_cmd(host, user, "virsh -c qemu:///system pool-info default", check=False)
    if pool_check.returncode != 0:
        ssh_cmd(host, user, "mkdir -p /var/lib/libvirt/images")
        define_result = ssh_cmd(
            host, user,
            "virsh -c qemu:///system pool-define-as default dir --target /var/lib/libvirt/images",
            check=False
        )
        if define_result.returncode != 0 and "already exists" not in define_result.stderr.lower():
            raise DeployError(f"Failed to define storage pool: {define_result.stderr}")
        print("  Default storage pool defined.")
    else:
        print("  Default storage pool already exists.")

    start_result = ssh_cmd(host, user, "virsh -c qemu:///system pool-start default", check=False)
    if start_result.returncode != 0 and "already active" not in start_result.stderr.lower():
        raise DeployError(f"Failed to start storage pool: {start_result.stderr}")

    ssh_cmd(host, user, "virsh -c qemu:///system pool-autostart default", check=False)
    print("  Storage pool ready.")

    result = ssh_cmd(host, user, "virsh -c qemu:///system list --all", check=False)
    if result.returncode != 0:
        raise DeployError("libvirt is not working on the remote host after setup")

    print("  Ensuring oc client is installed...")
    oc_check = ssh_cmd(host, user, "command -v oc", check=False)
    if oc_check.returncode != 0:
        ssh_cmd(
            host, user,
            "curl -sL https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/stable/openshift-client-linux.tar.gz | tar xzf - -C /usr/local/bin oc kubectl"
        )
        print("  oc client installed.")
    else:
        print("  oc client already installed.")

    print(f"Remote host {host} setup complete!")


def get_kcli_client_name(host: str) -> str:
    return host.split(".")[0]


def _create_ssh_config(host: str, user: str, key_path: str) -> None:
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    ssh_config_file = ssh_dir / "config"

    ssh_config_lines = [
        f"Host {host}",
        f"    User {user}",
        f"    IdentityFile {key_path}",
        "    StrictHostKeyChecking no",
        "    UserKnownHostsFile /dev/null",
        "    LogLevel ERROR",
        "    ConnectTimeout 30",
        "    ServerAliveInterval 10",
        "    ServerAliveCountMax 3",
        "    BatchMode yes",
    ]

    ssh_config_content = "\n".join(ssh_config_lines) + "\n\n"

    if ssh_config_file.exists():
        existing_config = ssh_config_file.read_text()
        pattern = rf"Host {re.escape(host)}\n(?:    .*\n)*\n?"
        existing_config = re.sub(pattern, "", existing_config)
        ssh_config_file.write_text(existing_config + ssh_config_content)
    else:
        ssh_config_file.write_text(ssh_config_content)

    ssh_config_file.chmod(0o600)
    print(f"  Created SSH config entry for {host}")


def _create_ssh_wrapper(key_path: str) -> None:
    """Set up ssh-agent with the key for kcli (which uses libssh)."""
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)

    default_key = ssh_dir / "id_rsa"
    key_content = Path(key_path).read_bytes()
    default_key.write_bytes(key_content)
    default_key.chmod(0o600)
    print(f"  Copied SSH key to {default_key}")

    pub_key = ssh_dir / "id_rsa.pub"
    if not pub_key.exists():
        print(f"  Generating public key: {pub_key}")
        result = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(default_key)],
            capture_output=True,
            text=True,
            check=True
        )
        pub_key.write_text(result.stdout)
        pub_key.chmod(0o644)

    if not os.environ.get("SSH_AUTH_SOCK"):
        print("  Starting ssh-agent...")
        result = subprocess.run(
            ["ssh-agent", "-s"],
            capture_output=True,
            text=True,
            check=True
        )
        for line in result.stdout.split('\n'):
            if '=' in line and not line.startswith('echo'):
                var_assignment = line.split(';')[0].strip()
                if '=' in var_assignment:
                    key, value = var_assignment.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    os.environ[key] = value
                    print(f"    Set {key}")
    else:
        print(f"  Using existing ssh-agent: {os.environ['SSH_AUTH_SOCK']}")

    print("  Adding SSH key to ssh-agent")
    subprocess.run(
        ["ssh-add", str(default_key)],
        capture_output=True,
        text=True,
        check=True
    )
    print("    SSH key added to agent")


def configure_kcli_remote_client(host: str, user: str) -> str:
    import yaml
    import os

    client_name = get_kcli_client_name(host)

    home_dir = Path(os.environ.get("HOME", str(Path.home())))
    kcli_dir = home_dir / ".kcli"
    kcli_dir.mkdir(parents=True, exist_ok=True)
    config_file = kcli_dir / "config.yml"

    config = {}
    if config_file.exists():
        config = yaml.safe_load(config_file.read_text()) or {}

    config.pop(client_name, None)

    client_config = {
        "host": host,
        "user": user,
        "protocol": "ssh",
        "pool": "default",
        "type": "kvm",
    }

    config[str(client_name)] = client_config

    yaml_content = yaml.dump(config, default_flow_style=False, allow_unicode=True)
    if client_name.isdigit():
        yaml_content = yaml_content.replace(f"{client_name}:", f"'{client_name}':")
    config_file.write_text(yaml_content)
    print(f"kcli client '{client_name}' configured for {user}@{host}")

    if _ssh_mod.ssh_key_path:
        if Path(_ssh_mod.ssh_key_path).is_absolute():
            abs_key_path = _ssh_mod.ssh_key_path
        else:
            abs_key_path = str(Path(_ssh_mod.ssh_key_path).absolute())
        print(f"Configuring SSH wrapper for kcli to use key: {abs_key_path}")

        _create_ssh_config(host, user, abs_key_path)
        _create_ssh_wrapper(abs_key_path)

    print(f"Verifying kcli connection to {host}...")
    result = run(["kcli", "-C", client_name, "list", "vm"], check=False, capture_output=True)
    if result.returncode != 0:
        error_msg = f"kcli cannot connect to remote host '{client_name}'.\n"
        if result.stderr:
            error_msg += f"Error: {result.stderr}\n"
        error_msg += f"\nDebug: Try manually:\n  ssh {host} 'echo test'\n  kcli -C {client_name} list vm"
        raise DeployError(error_msg)

    print("  kcli connection verified")
    return client_name


def setup_remote_cluster_access(
    host: str,
    user: str,
    cluster_name: str,
    api_ip: str,
    domain: str,
) -> None:
    kubeconfig_path = Path.home() / ".kcli" / "clusters" / cluster_name / "auth" / "kubeconfig"

    timeout = 120
    start = time.time()
    while not kubeconfig_path.exists():
        if time.time() - start > timeout:
            raise DeployError(f"Timeout waiting for kubeconfig at {kubeconfig_path}")
        print(f"  Waiting for kubeconfig... ({int(time.time() - start)}s)")
        time.sleep(5)

    scp_cmd(str(kubeconfig_path), f"{user}@{host}:/root/kubeconfig")

    api_hostname = f"api.{cluster_name}.{domain}"
    ssh_cmd(
        host, user,
        f"grep -q '{api_hostname}' /etc/hosts || echo '{api_ip} {api_hostname}' >> /etc/hosts",
        check=False
    )
    print("  Remote host configured for cluster access.")


def wait_for_cluster_ready(
    host: str,
    user: str,
    api_ip: str,
    timeout: int = 3600,
) -> bool:
    print(f"Waiting for cluster to be ready (timeout: {timeout}s)...")

    start_time = time.time()
    api_ready = False

    while True:
        elapsed = int(time.time() - start_time)

        if elapsed >= timeout:
            raise DeployError(f"Timeout waiting for cluster to be ready after {timeout}s")

        if not api_ready:
            result = ssh_cmd(host, user, f"curl -sk https://{api_ip}:6443/version", check=False)
            if "gitVersion" in result.stdout:
                print(f"  Kubernetes API is responding! ({elapsed}s)")
                api_ready = True
            else:
                print(f"  Waiting for Kubernetes API... ({elapsed}s)")
                time.sleep(30)
                continue

        cv_result = ssh_cmd(
            host, user,
            "export KUBECONFIG=/root/kubeconfig; "
            "oc get clusterversion version --no-headers 2>/dev/null || echo ''",
            check=False
        ).stdout.strip()

        cv_available = ""
        cv_progressing = ""
        if cv_result:
            parts = cv_result.split()
            if len(parts) >= 4:
                cv_available = parts[2]
                cv_progressing = parts[3]

        if cv_available == "True" and cv_progressing == "False":
            print(f"\n{'='*50}")
            print("SUCCESS! Cluster is ready!")
            print(f"{'='*50}")
            return True

        node_status = ssh_cmd(
            host, user,
            "export KUBECONFIG=/root/kubeconfig; oc get nodes --no-headers 2>/dev/null | head -1",
            check=False
        ).stdout.strip()

        print(f"  Cluster status: Available={cv_available or 'Unknown'}, Progressing={cv_progressing or 'Unknown'} ({elapsed}s)")
        if node_status:
            print(f"  Node: {node_status}")

        time.sleep(30)


def get_cluster_status(host: str, user: str) -> str:
    result = ssh_cmd(
        host, user,
        "export KUBECONFIG=/root/kubeconfig; "
        "oc get clusterversion 2>/dev/null; echo ''; "
        "oc get nodes 2>/dev/null; echo ''; "
        "oc get co 2>/dev/null | head -20",
        check=False
    )
    return result.stdout


def print_access_instructions(
    host: str,
    user: str,
    cluster_name: str,
    api_ip: str,
    domain: str,
    kcli_client: str,
) -> None:
    kubeconfig_path = Path.home() / ".kcli" / "clusters" / cluster_name / "auth"
    password_file = kubeconfig_path / "kubeadmin-password"

    password = "see kubeadmin-password file"
    if password_file.exists():
        password = password_file.read_text().strip()

    print(f"""
{'='*60}
ACCESS INSTRUCTIONS
{'='*60}

Kubeconfig (local): {kubeconfig_path / 'kubeconfig'}
Kubeconfig (remote): /root/kubeconfig on {host}
Kubeadmin password: {password}

To run oc commands via remote host:
  ssh {user}@{host} 'export KUBECONFIG=/root/kubeconfig; oc get nodes'

To access from your local machine, set up an SSH tunnel:
  ssh -L 6443:{api_ip}:6443 -L 443:{api_ip}:443 {user}@{host} -N &
  echo '127.0.0.1 api.{cluster_name}.{domain}' | sudo tee -a /etc/hosts
  export KUBECONFIG={kubeconfig_path / 'kubeconfig'}
  oc get nodes

{'='*60}
To delete the cluster:
  kcli -C {kcli_client} delete cluster {cluster_name} -y
{'='*60}
""")


def attach_pci_devices(
    host: str,
    user: str,
    vm_name: str,
    pci_devices: list[str],
    pre_start_hook: Optional[Callable[[], None]] = None,
) -> None:
    """Shut down VM, attach PCI devices for GPU passthrough, then restart."""
    print(f"Attaching {len(pci_devices)} PCI device(s) to VM '{vm_name}'...")

    vm_state = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
    if vm_state.returncode != 0 or "no state" in vm_state.stderr.lower():
        raise DeployError(f"VM '{vm_name}' not found on {host}. Cannot attach PCI devices.")

    was_running = "running" in vm_state.stdout.lower()

    if was_running:
        print(f"  Shutting down VM '{vm_name}'...")
        ssh_cmd(host, user, f"virsh shutdown {vm_name}", check=False)

        for i in range(24):
            time.sleep(5)
            state = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
            if "shut off" in state.stdout.lower():
                print("  VM shut down successfully.")
                break
            if i == 23:
                print("  Force stopping VM...")
                ssh_cmd(host, user, f"virsh destroy {vm_name}", check=False)
                time.sleep(2)

    for pci_addr in pci_devices:
        print(f"  Attaching PCI device: {pci_addr}")

        parts = pci_addr.replace(":", " ").replace(".", " ").split()
        if len(parts) != 4:
            raise DeployError(f"Invalid PCI address: {pci_addr} (expected 0000:XX:YY.Z)")

        domain, bus, slot, function = parts

        try:
            for part in (domain, bus, slot, function):
                int(part, 16)
        except ValueError as err:
            raise DeployError(f"Invalid PCI address: {pci_addr} (non-hex component)") from err

        xml_file = f"/tmp/pci-{pci_addr.replace(':', '-').replace('.', '-')}.xml"
        xml_content = (
            f"<hostdev mode='subsystem' type='pci' managed='yes'>"
            f"<source>"
            f"<address domain='0x{domain}' bus='0x{bus}' slot='0x{slot}' function='0x{function}'/>"
            f"</source>"
            f"</hostdev>"
        )
        xml_b64 = base64.b64encode(xml_content.encode()).decode()
        ssh_cmd(host, user, f"echo {xml_b64} | base64 -d > {xml_file}", check=True)

        result = ssh_cmd(host, user, f"virsh attach-device {vm_name} {xml_file} --config", check=False)
        if result.returncode != 0:
            if "already exists" in result.stderr.lower() or "already attached" in result.stderr.lower():
                print(f"    Device {pci_addr} already attached.")
            else:
                raise DeployError(f"Failed to attach PCI device {pci_addr}: {result.stderr}")
        else:
            print(f"    Device {pci_addr} attached.")

        ssh_cmd(host, user, f"rm -f {xml_file}", check=False)

    print("  Verifying PCI devices in VM config...")
    result = ssh_cmd(host, user, f"virsh dumpxml {vm_name} | grep -c hostdev", check=False)
    hostdev_count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    print(f"    Found {hostdev_count} hostdev entries in VM config.")

    if pre_start_hook:
        pre_start_hook()

    print(f"  Starting VM '{vm_name}'...")
    ssh_cmd(host, user, f"virsh start {vm_name}", check=True)

    vm_started = False
    for _ in range(12):
        time.sleep(5)
        state = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
        if "running" in state.stdout.lower():
            print(f"  VM '{vm_name}' is running with PCI passthrough enabled.")
            vm_started = True
            break

    if not vm_started:
        raise DeployError(f"VM '{vm_name}' failed to start after PCI device attachment.")

    print("PCI device attachment complete.")
