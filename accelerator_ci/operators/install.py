"""OLM operator installation primitives."""

from __future__ import annotations

import json
import logging
import time

from accelerator_ci.operators.errors import OperatorError
from accelerator_ci.shared.oc_runner import OcRunner

logger = logging.getLogger(__name__)


def ensure_namespace(oc: OcRunner, name: str) -> None:
    r = oc.oc("get", "namespace", name, timeout=10)
    if r.returncode == 0:
        return
    r = oc.oc("create", "namespace", name, timeout=10)
    if r.returncode != 0:
        raise OperatorError(f"Failed to create namespace {name}: {r.stderr or r.stdout}")


def create_operator_group(
    oc: OcRunner,
    namespace: str,
    name: str,
    all_namespaces: bool = False,
) -> None:
    manifest: dict = {
        "apiVersion": "operators.coreos.com/v1",
        "kind": "OperatorGroup",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {} if all_namespaces else {"targetNamespaces": [namespace]},
    }
    oc.apply_yaml(json.dumps(manifest))


def create_subscription(
    oc: OcRunner,
    namespace: str,
    name: str,
    package: str,
    catalog: str,
    channel: str,
    starting_csv: str | None = None,
    manual_approval: bool = False,
) -> None:
    spec: dict = {
        "channel": channel,
        "installPlanApproval": "Manual" if manual_approval else "Automatic",
        "name": package,
        "source": catalog,
        "sourceNamespace": "openshift-marketplace",
    }
    if starting_csv:
        spec["startingCSV"] = starting_csv
    manifest = {
        "apiVersion": "operators.coreos.com/v1alpha1",
        "kind": "Subscription",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }
    oc.apply_yaml(json.dumps(manifest))


def approve_install_plan(
    oc: OcRunner, namespace: str, csv_name: str, timeout: int = 300
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc(
            "get", "installplan", "-n", namespace, "-o", "json",
            timeout=15,
        )
        if r.returncode == 0 and r.stdout:
            try:
                items = json.loads(r.stdout).get("items", [])
            except json.JSONDecodeError:
                items = []
            for ip in items:
                csvs = (ip.get("spec") or {}).get("clusterServiceVersionNames") or []
                approved = (ip.get("spec") or {}).get("approved", False)
                if csv_name in csvs and not approved:
                    ip_name = ip["metadata"]["name"]
                    logger.info("Approving InstallPlan %s for %s...", ip_name, csv_name)
                    patch_r = oc.oc(
                        "patch", "installplan", ip_name, "-n", namespace,
                        "--type", "merge", "-p", '{"spec":{"approved":true}}',
                        timeout=15,
                    )
                    if patch_r.returncode != 0:
                        logger.warning("Patch failed (rc=%d): %s, retrying...", patch_r.returncode, patch_r.stderr or patch_r.stdout)
                        break
                    return
        time.sleep(10)
    raise OperatorError(f"Timeout ({timeout}s) waiting for InstallPlan for {csv_name}")


def wait_for_csv(oc: OcRunner, namespace: str, timeout: int = 600) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc(
            "get", "csv", "-n", namespace, "-o", "jsonpath={.items[*].status.phase}",
            timeout=30,
        )
        if r.returncode != 0:
            time.sleep(15)
            continue
        phases = (r.stdout or "").split()
        if not phases:
            time.sleep(15)
            continue
        if all(p == "Succeeded" for p in phases):
            return
        if "Failed" in phases:
            r2 = oc.oc("get", "csv", "-n", namespace, "-o", "yaml", timeout=10)
            raise OperatorError(
                f"CSV in {namespace} failed: {r2.stdout or 'check oc get csv -n ' + namespace}"
            )
        logger.info("Waiting for operator CSV in %s... (%s)", namespace, phases)
        time.sleep(15)
    raise OperatorError(f"Timeout ({timeout}s) waiting for CSV in {namespace}")


def wait_for_subscription_installed(
    oc: OcRunner, namespace: str, subscription_name: str, timeout: int = 600
) -> str:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc(
            "get", "subscription", subscription_name, "-n", namespace, "-o", "json",
            timeout=15,
        )
        if r.returncode != 0:
            time.sleep(10)
            continue
        try:
            sub = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            time.sleep(10)
            continue
        conditions = (sub.get("status") or {}).get("conditions") or []
        for c in conditions:
            if c.get("type") == "ResolutionFailed" and c.get("status") == "True":
                msg = c.get("message") or "Subscription resolution failed."
                raise OperatorError(
                    f"Operator subscription '{subscription_name}' failed: {msg}"
                )
        installed = (sub.get("status") or {}).get("installedCSV", "").strip()
        if installed:
            return installed
        logger.info("Waiting for subscription %s to resolve...", subscription_name)
        time.sleep(10)
    raise OperatorError(
        f"Timeout ({timeout}s) waiting for subscription {subscription_name} to install (no installedCSV)."
    )


def wait_for_csv_by_name(
    oc: OcRunner, namespace: str, csv_name: str, timeout: int = 600
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc(
            "get", "csv", csv_name, "-n", namespace,
            "-o", "jsonpath={.status.phase}",
            timeout=10,
        )
        if r.returncode == 0:
            phase = (r.stdout or "").strip()
            if phase == "Succeeded":
                return
            if phase == "Failed":
                r2 = oc.oc("get", "csv", csv_name, "-n", namespace, "-o", "yaml", timeout=10)
                raise OperatorError(f"CSV {csv_name} failed: {r2.stdout or 'check oc get csv'}")
        time.sleep(10)
    raise OperatorError(f"Timeout ({timeout}s) waiting for CSV {csv_name} to reach Succeeded.")


def wait_for_crd(oc: OcRunner, crd_name: str, timeout: int = 120) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = oc.oc(
            "get", "crd", crd_name,
            "-o", "jsonpath={.status.conditions[?(@.type==\"Established\")].status}",
            timeout=15,
        )
        if r.returncode == 0 and (r.stdout or "").strip() == "True":
            return
        time.sleep(5)
    raise OperatorError(f"Timeout ({timeout}s) waiting for CRD {crd_name}.")
