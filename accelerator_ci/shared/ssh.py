"""SSH/SCP utilities with multiplexing for CI use."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


SSH_CONTROL_PATH = "/tmp/ssh-mux-%r@%h:%p"

SSH_BASE_OPTS_LIST: list[str] = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=30",
    "-o", "ServerAliveInterval=10",
    "-o", "ServerAliveCountMax=3",
    "-o", "BatchMode=yes",
    "-o", f"ControlPath={SSH_CONTROL_PATH}",
    "-o", "ControlMaster=auto",
    "-o", "ControlPersist=600",
]

SSH_BASE_OPTS = " ".join(SSH_BASE_OPTS_LIST)

ssh_key_path: str | None = None


def set_ssh_key_path(key_path: str | None) -> None:
    global ssh_key_path

    if key_path:
        key_file = Path(key_path)

        if not key_file.exists():
            raise FileNotFoundError(f"SSH key file not found: {key_path}")

        current_mode = key_file.stat().st_mode
        if current_mode & 0o777 != 0o600:
            logger.debug("Fixing SSH key permissions: %s (chmod 600)", key_path)
            key_file.chmod(0o600)

    ssh_key_path = key_path


def _ssh_opts_list() -> list[str]:
    opts = list(SSH_BASE_OPTS_LIST)
    if ssh_key_path:
        opts += ["-i", ssh_key_path]
    return opts


def get_ssh_opts() -> str:
    """Return SSH options as a single string for callers that build shell commands."""
    return " ".join(_ssh_opts_list())


def ssh_cmd(
    host: str,
    user: str,
    command: str,
    check: bool = True,
    timeout: int = 300,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    cmd = ["ssh", *_ssh_opts_list(), f"{user}@{host}", command]
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input,
        )
    except subprocess.TimeoutExpired:
        logger.warning("SSH command timed out after %ds: %s", timeout, command[:80])
        if check:
            raise subprocess.CalledProcessError(
                124, cmd,
                output="", stderr=f"SSH command timed out after {timeout}s",
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=1,
            stdout="", stderr=f"SSH command timed out after {timeout}s",
        )


def scp_cmd(
    src: str,
    dest: str,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    cmd = ["scp", *_ssh_opts_list(), src, dest]
    try:
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"SCP timed out after {timeout}s: {src} -> {dest}") from None


def close_ssh_multiplexing(host: str, user: str) -> None:
    cmd = ["ssh", *_ssh_opts_list(), "-O", "exit", f"{user}@{host}"]
    subprocess.run(cmd, capture_output=True, timeout=10)
