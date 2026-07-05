"""Shared pytest fixtures for GPU operator verification tests."""

from __future__ import annotations

import logging
import os

import pytest
from kubernetes import client, config

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def load_kubeconfig():
    """Load Kubernetes configuration once per session.

    When ``KUBECONFIG`` is set explicitly, honour it even when running
    inside a cluster so that tests target the intended cluster.
    """
    kubeconfig_env = os.environ.get("KUBECONFIG")
    if kubeconfig_env:
        try:
            config.load_kube_config(config_file=kubeconfig_env)
            return
        except config.ConfigException as exc:
            pytest.fail(
                f"KUBECONFIG is set ({kubeconfig_env}) but could not be loaded: {exc}"
            )
    try:
        config.load_incluster_config()
        return
    except config.ConfigException:
        pass
    try:
        config.load_kube_config()
    except config.ConfigException as exc:
        pytest.fail(
            f"Cannot load Kubernetes config. "
            f"Set KUBECONFIG or run inside a cluster. Error: {exc}"
        )


@pytest.fixture(scope="session")
def k8s_core_api(load_kubeconfig) -> client.CoreV1Api:
    return client.CoreV1Api()


@pytest.fixture(scope="session")
def k8s_custom_api(load_kubeconfig) -> client.CustomObjectsApi:
    return client.CustomObjectsApi()
