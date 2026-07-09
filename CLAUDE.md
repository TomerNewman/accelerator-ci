# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-vendor CI framework for testing GPU accelerator operators on OpenShift. Pip-installable package (`accelerator-ci`) that provides cluster provisioning, OLM operator installation, and GPU test infrastructure. Vendor repos depend on this package and implement the `VendorProfile` interface.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Common Commands

```bash
# CLI (installed via pip install -e .)
accelerator-ci --config cluster-config.yaml deploy
accelerator-ci --config cluster-config.yaml --vendor-module my_vendor.profile operators
accelerator-ci --config cluster-config.yaml --vendor-module my_vendor.profile test-gpu
accelerator-ci --config cluster-config.yaml status
accelerator-ci --config cluster-config.yaml --dry-run deploy

# BYOC (skip deploy/delete, use existing cluster)
accelerator-ci --config config.yaml --kubeconfig ~/.kube/config --vendor-module my_vendor.profile operators

# Or via make
make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml
make cluster-operators CONFIG_FILE_PATH=cluster-config.yaml VENDOR_MODULE=my_vendor.profile
make test
```

## Architecture

### Package: `accelerator_ci`

Single top-level Python package, pip-installable via `pyproject.toml`. Vendor repos install it with `pip install git+https://...`.

- **`cluster_provision/`** — Cluster lifecycle. `main.py` is the CLI entrypoint (installed as `accelerator-ci` console script). Dispatches commands: deploy, delete, operators, test-gpu, cleanup, must-gather, status. Config is validated with Pydantic.

- **`operators/`** — Generic OLM installation framework:
  - `orchestrator.py` — Vendor-agnostic install flow, delegates to `VendorProfile`
  - `install.py` — OLM primitives (subscriptions, CSVs, CRDs)
  - `cluster_health.py` — Node readiness, MCP health, cluster stability checks
  - `prerequisites.py` — Cluster prerequisite verification

- **`shared/`** — Cross-cutting utilities:
  - `oc_runner.py` — `OcRunner` ABC with `LocalOcRunner` and `RemoteOcRunner`
  - `ssh.py` — SSH/SCP with multiplexing
  - `version_utils.py` — Semver comparison

- **`vendors/`** — `base.py` defines `VendorProfile` ABC and `OperatorSpec` dataclass

- **`testing/`** — Generic test infrastructure:
  - `helpers.py` — Pod lifecycle, GPU workload helpers (parameterized by resource name)
  - `runner.py` — pytest execution (local and SSH tunnel)
  - `conftest.py` — Shared K8s client fixtures

### Vendor Profile Pattern

Vendor repos implement `accelerator_ci.vendors.base.VendorProfile`. The CLI loads vendor profiles dynamically via `--vendor-module`:

```bash
accelerator-ci --config config.yaml --vendor-module my_vendor.profile operators
```

This imports `my_vendor.profile` and looks for a `VendorProfile` or `Profile` class.

## Key Patterns

- All imports use fully-qualified `accelerator_ci.*` paths.
- `OcRunner.oc()` returns `subprocess.CompletedProcess` — callers check `returncode`.
- All cluster operations support local and remote modes via the `remote` config section.
- The `vendor` field in config is informational; the actual profile is loaded via `--vendor-module`.
