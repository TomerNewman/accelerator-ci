"""Verify that the example vendor operator stack is healthy.

These tests run against a live cluster after the operators command completes.
The ``load_kubeconfig`` fixture is provided by the accelerator-ci pytest plugin.
"""

from __future__ import annotations

import time

import pytest
from kubernetes import client


@pytest.fixture
def core_api(load_kubeconfig):
    return client.CoreV1Api()


@pytest.fixture
def custom_api(load_kubeconfig):
    return client.CustomObjectsApi()


class TestNFD:
    def test_nfd_pods_running(self, core_api):
        pods = core_api.list_namespaced_pod("openshift-nfd").items
        running = [p for p in pods if p.status.phase == "Running"]
        assert running, "No NFD pods are running in openshift-nfd"

    def test_nfd_labels_on_nodes(self, core_api):
        nodes = core_api.list_node().items
        labeled = []
        for node in nodes:
            nfd_keys = [k for k in (node.metadata.labels or {})
                        if k.startswith("feature.node.kubernetes.io/")]
            if nfd_keys:
                labeled.append(node.metadata.name)
        assert labeled, "No nodes have NFD labels"

    def test_nfd_cr_exists(self, custom_api):
        crs = custom_api.list_namespaced_custom_object(
            group="nfd.openshift.io", version="v1",
            namespace="openshift-nfd", plural="nodefeaturediscoveries",
        )
        assert crs["items"], "NodeFeatureDiscovery CR not found"


class TestNMState:
    def test_nmstate_pods_running(self, core_api):
        pods = core_api.list_namespaced_pod("openshift-nmstate").items
        running = [p for p in pods if p.status.phase == "Running"]
        assert running, "No NMState pods are running in openshift-nmstate"

    def test_nmstate_cr_exists(self, custom_api):
        crs = custom_api.list_cluster_custom_object(
            group="nmstate.io", version="v1", plural="nmstates",
        )
        assert crs["items"], "NMState CR not found"


class TestMetalLB:
    def test_metallb_pods_running(self, core_api):
        pods = core_api.list_namespaced_pod("metallb-system").items
        running = [p for p in pods if p.status.phase == "Running"]
        assert running, "No MetalLB pods are running in metallb-system"


class TestWorkload:
    def test_simple_pod_completes(self, core_api):
        ns = "default"
        name = "example-vendor-test"

        try:
            core_api.delete_namespaced_pod(name, ns)
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise

        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(name=name),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[
                    client.V1Container(
                        name="test",
                        image="registry.access.redhat.com/ubi9/ubi-minimal:latest",
                        command=["echo", "example vendor test passed"],
                    ),
                ],
            ),
        )
        core_api.create_namespaced_pod(ns, pod)

        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                p = core_api.read_namespaced_pod(name, ns)
                if p.status.phase in ("Succeeded", "Failed"):
                    break
                time.sleep(5)

            p = core_api.read_namespaced_pod(name, ns)
            logs = core_api.read_namespaced_pod_log(name, ns)
        finally:
            try:
                core_api.delete_namespaced_pod(name, ns)
            except client.exceptions.ApiException:
                pass

        assert p.status.phase == "Succeeded", f"Pod failed: {logs}"
        assert "example vendor test passed" in logs
