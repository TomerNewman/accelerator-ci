"""Pod lifecycle and GPU workload helpers for verification tests."""

from __future__ import annotations

import logging
import time

from kubernetes import client
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

DEFAULT_POD_COMPLETION_TIMEOUT = 300
DEFAULT_POD_DELETION_TIMEOUT = 60
DEFAULT_POD_COMPLETION_POLL_INTERVAL = 5
DEFAULT_POD_DELETION_POLL_INTERVAL = 2


def delete_pod_if_exists(
    core_api: client.CoreV1Api,
    name: str,
    namespace: str,
    timeout: int = DEFAULT_POD_DELETION_TIMEOUT,
) -> None:
    """Delete a pod and block until it is gone."""
    try:
        core_api.delete_namespaced_pod(name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return
        raise

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            core_api.read_namespaced_pod(name, namespace)
            time.sleep(DEFAULT_POD_DELETION_POLL_INTERVAL)
        except ApiException as exc:
            if exc.status == 404:
                return
            raise
    raise TimeoutError(
        f"Pod {namespace}/{name} was not deleted within {timeout}s"
    )


def wait_for_pod_done(
    core_api: client.CoreV1Api,
    name: str,
    namespace: str,
    timeout: int = DEFAULT_POD_COMPLETION_TIMEOUT,
) -> str:
    """Wait for a pod to reach Succeeded or Failed and return the phase.

    Raises early with a descriptive message if the pod is stuck due to
    image-pull errors, scheduling failures, or other non-transient issues.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pod = core_api.read_namespaced_pod(name, namespace)
        phase = pod.status.phase
        if phase in ("Succeeded", "Failed"):
            return phase

        if phase == "Pending":
            check_pending_pod_errors(pod, name, namespace)

        time.sleep(DEFAULT_POD_COMPLETION_POLL_INTERVAL)

    pod = core_api.read_namespaced_pod(name, namespace)
    status_detail = describe_pod_status(pod)
    raise TimeoutError(
        f"Pod {namespace}/{name} did not complete within {timeout}s. "
        f"Current phase: {pod.status.phase}. {status_detail}"
    )


FATAL_WAITING_REASONS = frozenset({
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
})


def check_pending_pod_errors(
    pod: client.V1Pod,
    name: str,
    namespace: str,
) -> None:
    """Raise immediately if the pod is stuck for a non-transient reason."""
    for cs in pod.status.container_statuses or []:
        waiting = cs.state and cs.state.waiting
        if waiting and waiting.reason in FATAL_WAITING_REASONS:
            raise RuntimeError(
                f"Pod {namespace}/{name} cannot start: "
                f"{waiting.reason} — {waiting.message}"
            )

    for cond in pod.status.conditions or []:
        if (
            cond.type == "PodScheduled"
            and cond.status == "False"
            and cond.reason == "Unschedulable"
        ):
            raise RuntimeError(
                f"Pod {namespace}/{name} cannot be scheduled: "
                f"{cond.message}"
            )


def describe_pod_status(pod: client.V1Pod) -> str:
    """Return a short human-readable summary of a pod's current status."""
    parts: list[str] = []
    for cs in pod.status.container_statuses or []:
        waiting = cs.state and cs.state.waiting
        if waiting:
            parts.append(f"container '{cs.name}': {waiting.reason} — {waiting.message}")
    for cond in pod.status.conditions or []:
        if cond.status == "False":
            parts.append(f"condition {cond.type}: {cond.reason} — {cond.message}")
    return "; ".join(parts) if parts else "no additional detail available"


def run_gpu_command(
    core_api: client.CoreV1Api,
    pod_name: str,
    command: list[str],
    *,
    gpu_resource_name: str,
    namespace: str,
    image: str,
    gpu_count: str = "1",
    timeout: int = DEFAULT_POD_COMPLETION_TIMEOUT,
) -> str:
    """Create a privileged pod with a GPU, run *command*, and return its logs."""
    delete_pod_if_exists(core_api, pod_name, namespace)

    pod_body = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name, namespace=namespace),
        spec=client.V1PodSpec(
            restart_policy="Never",
            termination_grace_period_seconds=1,
            containers=[
                client.V1Container(
                    name=pod_name,
                    image=image,
                    command=command,
                    resources=client.V1ResourceRequirements(
                        requests={gpu_resource_name: gpu_count},
                        limits={gpu_resource_name: gpu_count},
                    ),
                    security_context=client.V1SecurityContext(
                        privileged=True,
                        allow_privilege_escalation=True,
                    ),
                ),
            ],
        ),
    )

    try:
        core_api.create_namespaced_pod(namespace, pod_body)
        logger.info("Created pod %s/%s", namespace, pod_name)

        phase = wait_for_pod_done(core_api, pod_name, namespace, timeout)
        logs = core_api.read_namespaced_pod_log(pod_name, namespace)
        logger.info("Pod %s finished with phase %s", pod_name, phase)

        assert phase == "Succeeded", (
            f"Pod {pod_name} failed (phase={phase}). Logs:\n{logs}"
        )
        return logs
    finally:
        delete_pod_if_exists(core_api, pod_name, namespace)
        logger.info("Cleaned up pod %s/%s", namespace, pod_name)
