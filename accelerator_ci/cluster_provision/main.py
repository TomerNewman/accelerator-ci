#!/usr/bin/env python3
"""CLI entrypoint for accelerator-ci."""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

from accelerator_ci.cluster_provision.config import (
    get_kcli_params,
    load_cluster_config,
    print_config,
)
from accelerator_ci.cluster_provision.params import update_version_to_latest_patch
from accelerator_ci.cluster_provision.deploy import deploy_cluster
from accelerator_ci.cluster_provision.delete import delete_cluster


def _kubeconfig_path(cluster_name: str) -> Path:
    return Path.home() / ".kcli" / "clusters" / cluster_name / "auth" / "kubeconfig"


def _load_vendor_profile(vendor_module: str):
    from accelerator_ci.vendors.base import VendorProfile as _BaseClass

    try:
        mod = importlib.import_module(vendor_module)
    except ImportError as e:
        print(f"Error: Could not import vendor module '{vendor_module}': {e}", file=sys.stderr)
        sys.exit(1)

    for attr in dir(mod):
        cls = getattr(mod, attr, None)
        if isinstance(cls, type) and issubclass(cls, _BaseClass) and cls is not _BaseClass:
            try:
                return cls()
            except TypeError:
                continue
    print(f"Error: No concrete VendorProfile subclass found in '{vendor_module}'", file=sys.stderr)
    sys.exit(1)


def _get_oc_runner(config):
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
    else:
        from accelerator_ci.shared.oc_runner import LocalOcRunner
        kubeconfig = _kubeconfig_path(config.cluster_name)
        if not kubeconfig.exists():
            print(f"Error: kubeconfig not found at {kubeconfig}", file=sys.stderr)
            sys.exit(1)
        return LocalOcRunner(kubeconfig)


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

    subparsers = parser.add_subparsers(dest="command", help="Action to perform")
    subparsers.add_parser("deploy", help="Deploy the OpenShift cluster")
    subparsers.add_parser("delete", help="Delete the OpenShift cluster")
    subparsers.add_parser("operators", help="Install GPU operator stack")
    subparsers.add_parser("test-gpu", help="Run GPU verification tests")
    subparsers.add_parser("cleanup", help="Remove GPU operator stack")
    subparsers.add_parser("must-gather", help="Collect diagnostic data")

    return parser.parse_args(argv)


def _require_vendor(args):
    if not args.vendor_module:
        print("Error: --vendor-module is required for this command.", file=sys.stderr)
        sys.exit(1)
    return _load_vendor_profile(args.vendor_module)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command
    if not command:
        print("Error: no command specified. Use one of: deploy, delete, operators, test-gpu, cleanup, must-gather", file=sys.stderr)
        return 1

    try:
        config = load_cluster_config(args.config_file)
    except (FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        return _dispatch(args, command, config)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _dispatch(args, command: str, config) -> int:
    if command == "deploy":
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
                print(f"Vendor provided {len(extra_devices)} PCI device(s): {extra_devices}")

        params = get_kcli_params(config, ocp_version)

        print_config(params)
        if pci_devices:
            print(f"PCI Passthrough Devices: {pci_devices}")
        print(f"Config file: {args.config_file}")

        deploy_cluster(
            params=params,
            remote_host=config.remote.host,
            pci_devices=pci_devices,
            remote_user=config.remote.user,
            wait_timeout=config.wait_timeout,
            ssh_key=config.remote.ssh_key_path,
        )

        artifact_dir = os.environ.get("ARTIFACT_DIR")
        if artifact_dir:
            artifact_path = Path(artifact_dir)
            artifact_path.mkdir(parents=True, exist_ok=True)
            (artifact_path / "ocp.version").write_text(ocp_version)
            print(f"Wrote ocp.version: {ocp_version}")

    elif command == "delete":
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
        oc = _get_oc_runner(config)

        machine_config_role = config.operators.machine_config_role
        if config.ctlplanes == 1 and config.workers == 0:
            machine_config_role = "master"

        install_operators(
            oc,
            vendor=vendor,
            vendor_config=config.operators.vendor_config,
            machine_config_role=machine_config_role,
            ocp_version=config.ocp_version,
        )

        if hasattr(oc, "close"):
            oc.close()

    elif command == "test-gpu":
        vendor = _require_vendor(args)
        test_path = vendor.get_test_path()

        kubeconfig = _kubeconfig_path(config.cluster_name)
        if not kubeconfig.exists():
            print(f"Error: kubeconfig not found at {kubeconfig}", file=sys.stderr)
            return 1

        if config.remote.host:
            from accelerator_ci.testing.runner import run_tests_remote
            rc = run_tests_remote(
                config.remote.host,
                config.remote.user,
                kubeconfig,
                test_path=test_path,
                ssh_key_path=config.remote.ssh_key_path,
            )
        else:
            from accelerator_ci.testing.runner import run_tests
            rc = run_tests(kubeconfig, test_path=test_path)
        return rc

    elif command == "cleanup":
        from accelerator_ci.operators.orchestrator import cleanup_operators

        vendor = _require_vendor(args)
        oc = _get_oc_runner(config)

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
            kubeconfig = _kubeconfig_path(config.cluster_name)
            if not kubeconfig.exists():
                print(f"Error: kubeconfig not found at {kubeconfig}", file=sys.stderr)
                return 1
            return run_must_gather(kubeconfig=str(kubeconfig), artifact_dir=artifact_dir)

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1
    return 0


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
