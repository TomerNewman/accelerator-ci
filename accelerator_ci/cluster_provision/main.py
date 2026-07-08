#!/usr/bin/env python3
"""CLI entrypoint for accelerator-ci."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from accelerator_ci.cluster_provision.config import (
    get_kcli_params,
    load_cluster_config,
    print_config,
    validate_deploy_config,
)
from accelerator_ci.cluster_provision.params import update_version_to_latest_patch
from accelerator_ci.cluster_provision.deploy import deploy_cluster
from accelerator_ci.cluster_provision.delete import delete_cluster

logger = logging.getLogger(__name__)


def _kubeconfig_path(cluster_name: str) -> Path:
    return Path.home() / ".kcli" / "clusters" / cluster_name / "auth" / "kubeconfig"


def _load_vendor_profile(vendor_module: str):
    from accelerator_ci.vendors.base import VendorProfile as _BaseClass

    try:
        mod = importlib.import_module(vendor_module)
    except ImportError as e:
        raise RuntimeError(
            f"Could not import vendor module '{vendor_module}': {e}"
        ) from e

    for attr in dir(mod):
        cls = getattr(mod, attr, None)
        if isinstance(cls, type) and issubclass(cls, _BaseClass) and cls is not _BaseClass:
            try:
                return cls()
            except TypeError:
                continue
    raise RuntimeError(
        f"No concrete VendorProfile subclass found in '{vendor_module}'"
    )


def _get_oc_runner(config, kubeconfig_override: str | None = None):
    if config.remote.host and config.remote.ssh_key_path:
        from accelerator_ci.shared.ssh import set_ssh_key_path
        set_ssh_key_path(config.remote.ssh_key_path)

    if config.remote.host:
        from accelerator_ci.shared.oc_runner import RemoteOcRunner, REMOTE_KUBECONFIG
        return RemoteOcRunner(
            host=config.remote.host,
            user=config.remote.user,
            remote_kubeconfig=REMOTE_KUBECONFIG,
        )

    from accelerator_ci.shared.oc_runner import LocalOcRunner
    kubeconfig = _resolve_kubeconfig(config.cluster_name, kubeconfig_override)
    return LocalOcRunner(kubeconfig)


def _resolve_kubeconfig(cluster_name: str, override: str | None = None) -> Path:
    if override:
        kc = Path(override).expanduser().resolve()
        if not kc.exists():
            raise FileNotFoundError(f"kubeconfig not found at {kc}")
        return kc
    kc = _kubeconfig_path(cluster_name)
    if not kc.exists():
        raise FileNotFoundError(f"kubeconfig not found at {kc}")
    return kc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage OpenShift cluster lifecycle and GPU operator installation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config cluster-config.yaml deploy
  %(prog)s --config cluster-config.yaml --vendor-module my_vendor.profile operators
""",
    )

    parser.add_argument(
        "-c", "--config",
        dest="config_file",
        required=True,
        help="Path to YAML configuration file.",
    )
    parser.add_argument(
        "--vendor-module",
        dest="vendor_module",
        help="Python module path to VendorProfile (e.g. 'my_vendor.profile'). "
             "Required for operators, test-gpu, and cleanup commands.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress informational output (WARNING and above only).",
    )
    parser.add_argument(
        "-n", "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show the execution plan without running anything.",
    )
    parser.add_argument(
        "--kubeconfig",
        help="Use an existing cluster instead of provisioning one. "
             "Skips deploy/delete; other commands run against this kubeconfig.",
    )
    parser.add_argument(
        "--json-progress",
        dest="json_progress",
        action="store_true",
        help="Emit JSON lines for each workflow step (for CI integration).",
    )

    subparsers = parser.add_subparsers(dest="command", help="Action to perform")
    subparsers.add_parser("deploy", help="Deploy the OpenShift cluster")
    subparsers.add_parser("delete", help="Delete the OpenShift cluster")
    subparsers.add_parser("operators", help="Install GPU operator stack")

    test_gpu_parser = subparsers.add_parser("test-gpu", help="Run GPU verification tests")
    test_gpu_parser.add_argument(
        "--junit-xml",
        dest="junit_xml",
        help="Path to write JUnit XML test results (e.g. results/junit.xml).",
    )

    subparsers.add_parser("cleanup", help="Remove GPU operator stack")
    subparsers.add_parser("must-gather", help="Collect diagnostic data")
    subparsers.add_parser("status", help="Show cluster health and GPU resources")

    return parser.parse_args(argv)


def _require_vendor(args):
    if not args.vendor_module:
        raise RuntimeError("--vendor-module is required for this command.")
    return _load_vendor_profile(args.vendor_module)


def _configure_logging(verbose: bool = False, quiet: bool = False) -> None:
    if verbose:
        level = logging.DEBUG
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    elif quiet:
        level = logging.WARNING
        fmt = "%(levelname)s: %(message)s"
    else:
        level = logging.INFO
        fmt = "%(message)s"

    logging.basicConfig(level=level, format=fmt, stream=sys.stdout, force=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    _configure_logging(verbose=args.verbose, quiet=args.quiet)

    command = args.command
    if not command:
        logger.error("Error: no command specified. Use one of: deploy, delete, operators, test-gpu, cleanup, must-gather, status")
        return 1

    try:
        config = load_cluster_config(args.config_file)
    except (FileNotFoundError, ValidationError) as e:
        logger.error("Error: %s", e)
        return 1

    try:
        return _dispatch(args, command, config)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        logger.error("Error: %s", e)
        return 1


def _dry_run_deploy(args, config) -> None:
    if args.kubeconfig:
        logger.info("Dry-run: deploy — SKIPPED (using external kubeconfig: %s)", args.kubeconfig)
        return

    ocp_version = update_version_to_latest_patch(config.ocp_version, config.version_channel)

    pci_devices = list(config.pci_devices)
    vendor_name = None
    if args.vendor_module:
        vendor = _load_vendor_profile(args.vendor_module)
        vendor_name = vendor.display_name
        host_args = dict(
            host=config.remote.host or "localhost",
            user=config.remote.user,
            ssh_key=config.remote.ssh_key_path,
            vendor_config=config.operators.vendor_config,
        )
        extra_devices = vendor.get_pci_devices(**host_args)
        if extra_devices:
            pci_devices.extend(extra_devices)

    target = f"remote ({config.remote.user}@{config.remote.host})" if config.remote.host else "local"
    topology = f"{config.ctlplanes} control-plane"
    if config.workers:
        topology += f", {config.workers} worker(s)"
    else:
        topology += " (SNO)"

    lines = [
        "Dry-run: deploy",
        f"  Cluster:      {config.cluster_name}",
        f"  OCP version:  {ocp_version} (channel: {config.version_channel})",
        f"  Domain:       {config.domain}",
        f"  Target:       {target}",
        f"  Topology:     {topology}",
        f"  API IP:       {config.api_ip}",
        f"  Network:      {config.network}",
        f"  Control-plane: {config.ctlplane.numcpus} vCPU, {config.ctlplane.memory} MB RAM",
        f"  Worker:       {config.worker.numcpus} vCPU, {config.worker.memory} MB RAM",
        f"  Disk:         {config.disk_size} GB",
    ]
    if vendor_name:
        lines.append(f"  Vendor:       {vendor_name} (host_setup will run)")
    if pci_devices:
        lines.append(f"  PCI devices:  {pci_devices}")
    if config.remote.host:
        lines.append("  Steps:")
        lines.append("    1. Setup remote libvirt")
        lines.append("    2. Configure kcli client")
        lines.append("    3. Push SSH key to remote host")
        lines.append("    4. Run kcli create cluster openshift")
        lines.append("    5. Setup remote cluster access (kubeconfig, /etc/hosts)")
        lines.append("    6. Wait for cluster ready")
        if pci_devices:
            lines.append("    7. Attach PCI devices to VMs")
    else:
        lines.append("  Steps:")
        lines.append("    1. Run kcli create cluster openshift")

    logger.info("%s", "\n".join(lines))


def _dry_run_delete(args, config) -> None:
    if args.kubeconfig:
        logger.info("Dry-run: delete — SKIPPED (using external cluster)")
        return

    target = f"remote ({config.remote.user}@{config.remote.host})" if config.remote.host else "local"
    lines = [
        "Dry-run: delete",
        f"  Cluster: {config.cluster_name}",
        f"  Target:  {target}",
    ]
    logger.info("%s", "\n".join(lines))


def _dry_run_operators(args, config) -> None:
    vendor = _require_vendor(args)
    ops = vendor.get_operators(config.operators.vendor_config)

    machine_config_role = config.operators.machine_config_role
    if config.ctlplanes == 1 and config.workers == 0:
        machine_config_role = "master"

    target = f"remote ({config.remote.user}@{config.remote.host})" if config.remote.host else "local"
    lines = [
        "Dry-run: operators",
        f"  Vendor:        {vendor.display_name}",
        f"  Target:        {target}",
        f"  MCP role:      {machine_config_role}",
        f"  OCP version:   {config.ocp_version}",
        "  Pre-flight:",
        "    - Verify required operators (marketplace, OLM)",
        "    - Configure internal registry",
        "    - Wait for cluster stability",
        "    - Run vendor pre_operator_setup",
        "    - Wait for MachineConfigPool update",
        f"  Operators ({len(ops)}):",
    ]
    for i, op in enumerate(ops, 1):
        lines.append(f"    {i}. {op.name}")
        lines.append(f"       package:   {op.package}")
        lines.append(f"       namespace: {op.namespace}")
        lines.append(f"       catalog:   {op.catalog}")
        lines.append(f"       channel:   {op.channel}")
        if op.manual_approval:
            lines.append(f"       approval:  manual (startingCSV: {op.starting_csv})")
    lines.append("  Post-install:")
    lines.append("    - Run vendor post_operator_setup")
    lines.append("    - Wait for cluster stability")
    lines.append("    - Wait for GPU readiness")

    logger.info("%s", "\n".join(lines))


def _dry_run_test_gpu(args, config) -> None:
    vendor = _require_vendor(args)
    test_path = vendor.get_test_path()
    junit_xml = getattr(args, "junit_xml", None)
    target = f"remote ({config.remote.user}@{config.remote.host})" if config.remote.host else "local"

    lines = [
        "Dry-run: test-gpu",
        f"  Vendor:    {vendor.display_name}",
        f"  Target:    {target}",
        f"  Test path: {test_path}",
    ]
    if junit_xml:
        lines.append(f"  JUnit XML: {junit_xml}")
    if config.remote.host:
        lines.append("  Steps:")
        lines.append("    1. Open SSH tunnel to cluster API")
        lines.append("    2. Rewrite kubeconfig for tunnel")
        lines.append("    3. Run pytest via SSH")
    else:
        lines.append("  Steps:")
        lines.append("    1. Run pytest locally")

    logger.info("%s", "\n".join(lines))


def _dry_run_cleanup(args, config) -> None:
    vendor = _require_vendor(args)
    target = f"remote ({config.remote.user}@{config.remote.host})" if config.remote.host else "local"
    lines = [
        "Dry-run: cleanup",
        f"  Vendor: {vendor.display_name}",
        f"  Target: {target}",
        "  Steps:",
        "    1. Run vendor cleanup()",
    ]
    logger.info("%s", "\n".join(lines))


def _dry_run_must_gather(args, config) -> None:
    artifact_dir = config.must_gather.artifact_dir
    target = f"remote ({config.remote.user}@{config.remote.host})" if config.remote.host else "local"
    lines = [
        "Dry-run: must-gather",
        f"  Artifact dir: {artifact_dir}",
        f"  Target:       {target}",
    ]
    if config.remote.host:
        lines.append("  Steps:")
        lines.append("    1. SCP must-gather script to remote host")
        lines.append("    2. Execute must-gather remotely")
        lines.append("    3. Stream artifacts back via tar pipeline")
    else:
        lines.append("  Steps:")
        lines.append("    1. Run must-gather.sh locally")

    logger.info("%s", "\n".join(lines))


def _dry_run_status(args, config) -> None:
    target = f"remote ({config.remote.user}@{config.remote.host})" if config.remote.host else "local"
    lines = [
        "Dry-run: status",
        f"  Cluster: {config.cluster_name}",
        f"  Target:  {target}",
        "  Queries:",
        "    - oc get clusterversion",
        "    - oc get nodes",
        "    - oc get csv -A",
        "    - oc get nodes -o json (GPU allocatable)",
    ]
    logger.info("%s", "\n".join(lines))


_DRY_RUN_HANDLERS = {
    "deploy": _dry_run_deploy,
    "delete": _dry_run_delete,
    "operators": _dry_run_operators,
    "test-gpu": _dry_run_test_gpu,
    "cleanup": _dry_run_cleanup,
    "must-gather": _dry_run_must_gather,
    "status": _dry_run_status,
}


def _dispatch(args, command: str, config) -> int:
    kc_override = args.kubeconfig

    if kc_override:
        _resolve_kubeconfig(config.cluster_name, kc_override)
        if config.remote.host:
            raise RuntimeError(
                "--kubeconfig and remote.host are mutually exclusive. "
                "Remove the remote section from your config or drop --kubeconfig."
            )

    if args.dry_run:
        handler = _DRY_RUN_HANDLERS.get(command)
        if not handler:
            logger.error("Unknown command: %s", command)
            return 1
        handler(args, config)
        return 0

    if command == "deploy":
        if kc_override:
            logger.info("Skipping deploy — using external kubeconfig: %s", kc_override)
            return 0

        validate_deploy_config(config)

        ocp_version = update_version_to_latest_patch(config.ocp_version, config.version_channel)

        pci_devices = list(config.pci_devices)

        if args.vendor_module:
            vendor = _load_vendor_profile(args.vendor_module)
            host_args = dict(
                host=config.remote.host or "localhost",
                user=config.remote.user,
                ssh_key=config.remote.ssh_key_path,
                vendor_config=config.operators.vendor_config,
            )
            vendor.host_setup(**host_args)
            extra_devices = vendor.get_pci_devices(**host_args)
            if extra_devices:
                pci_devices.extend(extra_devices)
                logger.info("Vendor provided %d PCI device(s): %s", len(extra_devices), extra_devices)

        params = get_kcli_params(config, ocp_version)

        print_config(params)
        if pci_devices:
            logger.info("PCI Passthrough Devices: %s", pci_devices)
        logger.info("Config file: %s", args.config_file)

        deploy_cluster(
            params=params,
            remote_host=config.remote.host,
            pci_devices=pci_devices,
            remote_user=config.remote.user,
            wait_timeout=config.wait_timeout,
            ssh_key=config.remote.ssh_key_path,
            json_progress=args.json_progress,
        )

        artifact_dir = os.environ.get("ARTIFACT_DIR")
        if artifact_dir:
            artifact_path = Path(artifact_dir)
            artifact_path.mkdir(parents=True, exist_ok=True)
            (artifact_path / "ocp.version").write_text(ocp_version)
            logger.info("Wrote ocp.version: %s", ocp_version)

    elif command == "delete":
        if kc_override:
            logger.info("Skipping delete — using external cluster")
            return 0

        params = {"cluster": config.cluster_name}

        delete_cluster(
            params=params,
            remote_host=config.remote.host,
            remote_user=config.remote.user,
            ssh_key=config.remote.ssh_key_path,
        )

    elif command == "operators":
        from accelerator_ci.operators.orchestrator import install_operators

        vendor = _require_vendor(args)
        oc = _get_oc_runner(config, kubeconfig_override=kc_override)

        machine_config_role = config.operators.machine_config_role
        if config.ctlplanes == 1 and config.workers == 0:
            machine_config_role = "master"

        install_operators(
            oc,
            vendor=vendor,
            vendor_config=config.operators.vendor_config,
            machine_config_role=machine_config_role,
            ocp_version=config.ocp_version,
            json_progress=args.json_progress,
        )

        if hasattr(oc, "close"):
            oc.close()

    elif command == "test-gpu":
        vendor = _require_vendor(args)
        test_path = vendor.get_test_path()
        junit_xml = getattr(args, "junit_xml", None)

        kubeconfig = _resolve_kubeconfig(config.cluster_name, kc_override)

        if config.remote.host:
            from accelerator_ci.testing.runner import run_tests_remote
            rc = run_tests_remote(
                config.remote.host,
                config.remote.user,
                kubeconfig,
                test_path=test_path,
                ssh_key_path=config.remote.ssh_key_path,
                junit_xml=junit_xml,
            )
        else:
            from accelerator_ci.testing.runner import run_tests
            rc = run_tests(kubeconfig, test_path=test_path, junit_xml=junit_xml)
        return rc

    elif command == "cleanup":
        from accelerator_ci.operators.orchestrator import cleanup_operators

        vendor = _require_vendor(args)
        oc = _get_oc_runner(config, kubeconfig_override=kc_override)

        cleanup_operators(oc, vendor=vendor)

        if hasattr(oc, "close"):
            oc.close()

    elif command == "must-gather":
        from accelerator_ci.cluster_provision.must_gather import run_must_gather, run_must_gather_remote

        artifact_dir = config.must_gather.artifact_dir

        if config.remote.host and config.remote.ssh_key_path:
            from accelerator_ci.shared.ssh import set_ssh_key_path
            set_ssh_key_path(config.remote.ssh_key_path)

        if config.remote.host:
            return run_must_gather_remote(
                host=config.remote.host,
                user=config.remote.user,
                artifact_dir=artifact_dir,
            )
        else:
            kubeconfig = _resolve_kubeconfig(config.cluster_name, kc_override)
            return run_must_gather(kubeconfig=str(kubeconfig), artifact_dir=artifact_dir)

    elif command == "status":
        from accelerator_ci.cluster_provision.status import print_status

        oc = _get_oc_runner(config, kubeconfig_override=kc_override)
        rc = print_status(oc)
        if hasattr(oc, "close"):
            oc.close()
        return rc

    else:
        logger.error("Unknown command: %s", command)
        return 1
    return 0


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
