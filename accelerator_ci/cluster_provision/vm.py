"""Low-level VM lifecycle operations via virsh over SSH."""

from __future__ import annotations

import base64
import logging
import re
import time

from accelerator_ci.shared.ssh import ssh_cmd

logger = logging.getLogger(__name__)


def vm_state(host: str, user: str, vm_name: str) -> str | None:
    """Return the VM state ('running', 'shut off', …) or None if not found."""
    r = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip()


def vm_exists(host: str, user: str, vm_name: str) -> bool:
    return vm_state(host, user, vm_name) is not None


def destroy_vm(host: str, user: str, vm_name: str) -> None:
    """Force power-off a VM. No-op if already off."""
    state = vm_state(host, user, vm_name)
    if state and state != "shut off":
        ssh_cmd(host, user, f"virsh destroy {vm_name}", check=False)
        time.sleep(2)


def shutdown_vm(host: str, user: str, vm_name: str, timeout: int = 120) -> None:
    """Graceful shutdown with fallback to force destroy."""
    state = vm_state(host, user, vm_name)
    if not state or state == "shut off":
        return

    ssh_cmd(host, user, f"virsh shutdown {vm_name}", check=False)

    for _ in range(timeout // 5):
        time.sleep(5)
        if vm_state(host, user, vm_name) == "shut off":
            logger.info("%s shut off", vm_name)
            return

    logger.warning("%s did not shut off in %ds — forcing destroy", vm_name, timeout)
    destroy_vm(host, user, vm_name)


def start_vm(host: str, user: str, vm_name: str) -> None:
    """Start a VM and wait until it is running."""
    ssh_cmd(host, user, f"virsh start {vm_name}", check=False)

    for _ in range(12):
        time.sleep(5)
        if vm_state(host, user, vm_name) == "running":
            logger.info("%s is running", vm_name)
            return

    raise RuntimeError(f"VM {vm_name} failed to start within 60s")


def shutdown_vms(host: str, user: str, cluster_name: str, ctlplanes: int) -> None:
    """Shut down all control-plane VMs."""
    for idx in range(ctlplanes):
        shutdown_vm(host, user, f"{cluster_name}-ctlplane-{idx}")


def start_vms(host: str, user: str, cluster_name: str, ctlplanes: int) -> None:
    """Start all control-plane VMs."""
    for idx in range(ctlplanes):
        start_vm(host, user, f"{cluster_name}-ctlplane-{idx}")


def attach_pci_devices(
    host: str,
    user: str,
    vm_name: str,
    pci_devices: list[str],
) -> None:
    """Attach PCI devices to a shut-off VM and start it."""
    logger.info("Attaching %d PCI device(s) to VM '%s'", len(pci_devices), vm_name)

    state = vm_state(host, user, vm_name)
    if state is None:
        raise RuntimeError(f"VM '{vm_name}' not found on {host}")

    if state != "shut off":
        shutdown_vm(host, user, vm_name)

    for pci_addr in pci_devices:
        parts = pci_addr.replace(":", " ").replace(".", " ").split()
        if len(parts) != 4:
            raise RuntimeError(f"Invalid PCI address: {pci_addr} (expected 0000:XX:YY.Z)")

        domain, bus, slot, function = parts
        try:
            for part in (domain, bus, slot, function):
                int(part, 16)
        except ValueError:
            raise RuntimeError(f"Invalid PCI address: {pci_addr} (expected 0000:XX:YY.Z)")

        xml_content = (
            f"<hostdev mode='subsystem' type='pci' managed='yes'>"
            f"<source>"
            f"<address domain='0x{domain}' bus='0x{bus}' "
            f"slot='0x{slot}' function='0x{function}'/>"
            f"</source>"
            f"</hostdev>"
        )
        xml_b64 = base64.b64encode(xml_content.encode()).decode()
        xml_file = f"/tmp/pci-{pci_addr.replace(':', '-').replace('.', '-')}.xml"
        ssh_cmd(host, user, f"echo {xml_b64} | base64 -d > {xml_file}", check=True)

        result = ssh_cmd(
            host, user,
            f"virsh attach-device {vm_name} {xml_file} --config",
            check=False,
        )
        if result.returncode != 0:
            if "already exists" in (result.stderr or "").lower():
                logger.info("Device %s already attached", pci_addr)
            else:
                raise RuntimeError(f"Failed to attach PCI device {pci_addr}: {result.stderr}")
        else:
            logger.info("Device %s attached", pci_addr)

        ssh_cmd(host, user, f"rm -f {xml_file}", check=False)

    start_vm(host, user, vm_name)


def detach_all_pci_devices(host: str, user: str, vm_name: str) -> None:
    """Detach all PCI hostdev devices from a VM (must be shut off)."""
    state = vm_state(host, user, vm_name)
    if state and state != "shut off":
        raise RuntimeError(
            f"VM '{vm_name}' must be shut off before detaching PCI devices "
            f"(current state: {state})"
        )

    r = ssh_cmd(host, user, f"virsh dumpxml {vm_name}", check=False)
    if r.returncode != 0 or not r.stdout:
        return

    matches = list(re.finditer(r"(<hostdev.*?</hostdev>)", r.stdout, re.DOTALL))
    if not matches:
        return

    logger.info("Detaching %d PCI device(s) from %s", len(matches), vm_name)
    for idx, match in enumerate(matches):
        hostdev_xml = match.group(1)
        xml_b64 = base64.b64encode(hostdev_xml.encode()).decode()
        tmp = f"/tmp/detach-hostdev-{idx}.xml"
        ssh_cmd(host, user, f"echo {xml_b64} | base64 -d > {tmp}", check=False)
        r = ssh_cmd(host, user, f"virsh detach-device {vm_name} {tmp} --config", check=False)
        if r.returncode != 0:
            logger.warning("Failed to detach hostdev: %s", (r.stderr or "").strip())
        ssh_cmd(host, user, f"rm -f {tmp}", check=False)
