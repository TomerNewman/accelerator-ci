"""oc command runner: local (subprocess) and remote (SSH)."""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from accelerator_ci.shared.ssh import ssh_cmd, scp_cmd, close_ssh_multiplexing

logger = logging.getLogger(__name__)

REMOTE_KUBECONFIG = "/root/kubeconfig"

_TRANSIENT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"connection refused",
        r"connection reset",
        r"connection timed out",
        r"i/o timeout",
        r"no route to host",
        r"unexpected EOF",
        r"broken pipe",
        r"unable to connect to the server",
        r"the server is currently unable to handle the request",
        r"context deadline exceeded",
        r"etcd leader changed",
        r"TLS handshake timeout",
        r"net/http: request canceled",
        r"error dialing backend",
        r"command timed out",
        r"\b5\d\d\b",
    )
]

DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 2


def _is_transient(result: subprocess.CompletedProcess) -> bool:
    if result.returncode == 0:
        return False
    if result.returncode == 124:
        return True
    combined = (result.stderr or "") + (result.stdout or "")
    return any(pat.search(combined) for pat in _TRANSIENT_PATTERNS)


def _retry_loop(run_fn, retries: int) -> subprocess.CompletedProcess:
    retries = max(retries, 0)
    for attempt in range(retries + 1):
        result = run_fn()
        if result.returncode == 0 or not _is_transient(result) or attempt == retries:
            return result
        delay = DEFAULT_RETRY_DELAY ** attempt
        logger.warning(
            "Transient oc error (attempt %d/%d), retrying in %ds: %s",
            attempt + 1, retries + 1, delay,
            (result.stderr or result.stdout or "").strip()[:120],
        )
        time.sleep(delay)
    raise AssertionError("unreachable")


class OcRunner(ABC):
    @abstractmethod
    def oc(
        self,
        *args: str,
        timeout: int | None = None,
        stdin: str | None = None,
        retries: int = DEFAULT_RETRIES,
    ) -> subprocess.CompletedProcess: ...

    @abstractmethod
    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None: ...


class LocalOcRunner(OcRunner):
    def __init__(self, kubeconfig_path: str | Path) -> None:
        self.kubeconfig = Path(kubeconfig_path).expanduser().resolve()
        if not self.kubeconfig.exists():
            raise RuntimeError(f"Kubeconfig not found: {self.kubeconfig}")

    def oc(
        self,
        *args: str,
        timeout: int | None = None,
        stdin: str | None = None,
        retries: int = DEFAULT_RETRIES,
    ) -> subprocess.CompletedProcess:
        env = {**os.environ, "KUBECONFIG": str(self.kubeconfig)}

        def _run() -> subprocess.CompletedProcess:
            try:
                return subprocess.run(
                    ["oc"] + list(args),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    input=stdin,
                )
            except subprocess.TimeoutExpired as e:
                return subprocess.CompletedProcess(
                    args=["oc"] + list(args),
                    returncode=124,
                    stdout=e.stdout or "",
                    stderr=f"Command timed out after {timeout}s",
                )

        return _retry_loop(_run, retries)

    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None:
        r = self.oc("apply", "-f", "-", timeout=timeout, stdin=yaml_content)
        if r.returncode != 0:
            raise RuntimeError(
                f"oc apply failed: {r.stderr or r.stdout or 'unknown error'}"
            )


class RemoteOcRunner(OcRunner):
    def __init__(
        self,
        host: str,
        user: str,
        remote_kubeconfig: str,
    ) -> None:
        self.host = host
        self.user = user
        self.remote_kubeconfig = remote_kubeconfig

    def oc(
        self,
        *args: str,
        timeout: int | None = None,
        stdin: str | None = None,
        retries: int = DEFAULT_RETRIES,
    ) -> subprocess.CompletedProcess:
        oc_cmd = " ".join(shlex.quote(a) for a in ("oc",) + args)
        full_cmd = f"KUBECONFIG={self.remote_kubeconfig} {oc_cmd}"

        def _run() -> subprocess.CompletedProcess:
            ssh_result = ssh_cmd(
                self.host,
                self.user,
                full_cmd,
                check=False,
                timeout=timeout or 300,
            )
            return subprocess.CompletedProcess(
                args=["oc"] + list(args),
                returncode=ssh_result.returncode,
                stdout=ssh_result.stdout,
                stderr=ssh_result.stderr,
            )

        return _retry_loop(_run, retries)

    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            local_path = f.name
        remote_path = f"/tmp/apply-{uuid.uuid4().hex}.yaml"
        try:
            scp_cmd(local_path, f"{self.user}@{self.host}:{remote_path}")
            r = self.oc("apply", "-f", remote_path, timeout=timeout)
            if r.returncode != 0:
                raise RuntimeError(
                    f"oc apply failed: {r.stderr or r.stdout or 'unknown error'}"
                )
        finally:
            Path(local_path).unlink(missing_ok=True)
            ssh_cmd(self.host, self.user, f"rm -f {remote_path}", check=False)

    def close(self) -> None:
        close_ssh_multiplexing(self.host, self.user)
