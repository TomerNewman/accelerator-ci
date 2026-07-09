# Accelerator CI

Multi-vendor CI framework for testing GPU accelerator operators on OpenShift. Provisions clusters via kcli/libvirt, installs operator stacks through OLM, and runs GPU verification tests.

Any GPU vendor can integrate by implementing the `VendorProfile` interface.

## Installation

```bash
pip install git+https://github.com/TomerNewman/accelerator-ci.git@main

# For development on accelerator-ci itself
git clone https://github.com/TomerNewman/accelerator-ci.git
cd accelerator-ci
pip install -e .
```

## Quick Start

```bash
# Full lifecycle
accelerator-ci --config cluster-config.yaml deploy
accelerator-ci --config cluster-config.yaml --vendor-module my_vendor.profile operators
accelerator-ci --config cluster-config.yaml --vendor-module my_vendor.profile test-gpu
accelerator-ci --config cluster-config.yaml --vendor-module my_vendor.profile cleanup
accelerator-ci --config cluster-config.yaml delete

# Preview what would happen
accelerator-ci --config cluster-config.yaml --dry-run deploy

# Use an existing cluster (skip deploy/delete)
accelerator-ci --config cluster-config.yaml --kubeconfig ~/.kube/config status
```

## Commands

```
deploy → operators → test-gpu → cleanup → delete
```

| Command | Description | Vendor needed? |
|---------|-------------|----------------|
| `deploy` | Provision an OpenShift cluster (local or remote libvirt) | No |
| `delete` | Destroy the cluster | No |
| `operators` | Install the GPU operator stack via OLM | Yes |
| `test-gpu` | Run GPU verification tests | Yes |
| `cleanup` | Remove the operator stack | Yes |
| `must-gather` | Collect diagnostic data | No |
| `status` | Show cluster version, nodes, operators, GPU resources | No |

## CLI Flags

| Flag | Description |
|------|-------------|
| `-c, --config` | Path to YAML config file (required) |
| `--vendor-module` | Python module with `VendorProfile` (e.g. `my_vendor.profile`) |
| `-n, --dry-run` | Preview the execution plan without running anything |
| `--kubeconfig` | Use an existing cluster; skips `deploy` and `delete` |
| `-v, --verbose` | Debug-level logging |
| `-q, --quiet` | Warning-level logging only |
| `--json-progress` | Emit JSON lines per workflow step (for CI tooling) |
| `--junit-xml` | Write JUnit XML results (on `test-gpu` only) |

## Configuration Reference

Only `cluster_name` and `ocp_version` are required. Everything else has defaults.

### Cluster Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cluster_name` | string | **required** | Name for the kcli cluster |
| `ocp_version` | string | **required** | OpenShift version (`"4.17"` resolves to latest patch) |
| `domain` | string | `"example.com"` | Cluster base domain (must change for deploy) |
| `pull_secret_path` | string | `""` | Path to Red Hat pull secret JSON (required for deploy) |
| `api_ip` | string | `""` | Static IP for the API VIP (required for deploy) |
| `ctlplanes` | int | `1` | Number of control plane nodes |
| `workers` | int | `0` | Number of worker nodes (0 = SNO) |
| `disk_size` | int | `120` | VM disk size in GB |
| `network` | string | `"default"` | Libvirt network name |
| `version_channel` | string | `"stable"` | OCP release channel |
| `wait_timeout` | int | `3600` | Deploy timeout in seconds |
| `vendor` | string | `""` | Informational label (actual profile comes from `--vendor-module`) |

### Node Sizing

```yaml
ctlplane:
  numcpus: 8       # default: 4
  memory: 24576    # default: 8192 (MB)

worker:
  numcpus: 4       # default: 4
  memory: 16384    # default: 8192 (MB)
```

### Remote Deployment

Set `remote.host` to deploy on a remote libvirt hypervisor via SSH. Leave `null` for local deployment.

```yaml
remote:
  host: gpu-server.lab       # null = local
  user: root                 # default: root
  ssh_key_path: ~/.ssh/id_rsa
```

### PCI Passthrough

```yaml
pci_devices: [0000:41:00.0, 0000:42:00.0]
# Also accepts a comma-separated string:
# pci_devices: "0000:41:00.0, 0000:42:00.0"
```

Vendors can also supply PCI devices dynamically via `get_pci_devices()`.

### Operators

```yaml
operators:
  machine_config_role: worker   # "master" for SNO (auto-detected by CLI)

  # Everything below is vendor-specific and passed as a dict to the profile:
  gpu_operator_version: "24.3.0"
  driver_version: "550.127"
```

### Must-Gather

```yaml
must_gather:
  artifact_dir: ./must-gather-output
```

### Bring Your Own Cluster

For existing clusters, you only need two fields in your config:

```yaml
cluster_name: my-cluster
ocp_version: "4.17"
```

Then use `--kubeconfig`:

```bash
accelerator-ci --config minimal.yaml --kubeconfig ~/.kube/config --vendor-module my_vendor.profile operators
```

## Creating a Vendor Repo

### 1. Set up the repo

```bash
mkdir my-gpu-ci && cd my-gpu-ci
python3 -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/TomerNewman/accelerator-ci.git@main
```

`requirements.txt`:

```
accelerator-ci @ git+https://github.com/TomerNewman/accelerator-ci.git@main
```

### 2. Implement VendorProfile

`my_gpu_ci/profile.py`:

```python
import json
import time

from accelerator_ci.vendors.base import VendorProfile, OperatorSpec

class Profile(VendorProfile):

    @property
    def display_name(self) -> str:
        return "My GPU"

    def get_operators(self, vendor_config):
        channel = vendor_config.get("channel", "stable")
        return [
            OperatorSpec(
                name="NFD",
                package="nfd",
                namespace="openshift-nfd",
                catalog="redhat-operators",
                channel="stable",
            ),
            OperatorSpec(
                name="My GPU Operator",
                package="my-gpu-operator",
                namespace="my-gpu-system",
                catalog="certified-operators",
                channel=channel,
            ),
        ]

    def post_operator_setup(self, oc, vendor_config, ocp_version):
        cr = {
            "apiVersion": "my-gpu.io/v1",
            "kind": "GPUConfig",
            "metadata": {"name": "default", "namespace": "my-gpu-system"},
            "spec": {"driverVersion": vendor_config.get("driver_version", "latest")},
        }
        oc.apply_yaml(json.dumps(cr))

    def wait_for_gpu_ready(self, oc, timeout=900):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            r = oc.oc("get", "nodes", "-o",
                       "jsonpath={.items[*].status.allocatable.my-vendor\\.com/gpu}",
                       timeout=15)
            if r.returncode == 0 and r.stdout and r.stdout.strip() != "0":
                return
            time.sleep(30)
        raise RuntimeError(f"GPUs not detected within {timeout}s")
```

### 3. VendorProfile Methods

**Required** (must override):

| Method | When it runs | What to do |
|--------|-------------|------------|
| `display_name` | Logging | Return a human-readable name |
| `get_operators(vendor_config)` | `operators` command | Return ordered list of `OperatorSpec` |
| `post_operator_setup(oc, vendor_config, ocp_version)` | After all CSVs succeed | Create vendor CRs, NFD rules, etc. |
| `wait_for_gpu_ready(oc, timeout)` | After post-setup | Poll until GPU extended resources appear on nodes |

**Optional** (no-op by default):

| Method | When it runs | Example use |
|--------|-------------|-------------|
| `pre_operator_setup(oc, vendor_config, role)` | Before operator install | Blacklist in-tree drivers via MachineConfig |
| `cleanup(oc)` | `cleanup` command | Delete CRs, subscriptions, namespaces |
| `get_test_path()` | `test-gpu` command | Return path to pytest directory (default: `"tests"`) |
| `host_setup(host, user, ssh_key, vendor_config)` | Before `deploy` | Install host drivers, enable IOMMU |
| `get_pci_devices(host, user, ssh_key, vendor_config)` | Before `deploy` | Return PCI addresses to passthrough |

### 4. OperatorSpec Fields

```python
OperatorSpec(
    name="GPU Operator",              # Display name for logs
    package="gpu-operator",           # OLM package name
    namespace="gpu-operator",         # Target namespace
    catalog="certified-operators",    # CatalogSource name
    channel="v24.3",                  # Subscription channel
    starting_csv="gpu-op.v24.3.0",   # Pin to a specific CSV (optional)
    manual_approval=False,            # Require manual InstallPlan approval
    all_namespaces=False,             # OperatorGroup targets all namespaces
)
```

If `manual_approval=True`, you must also set `starting_csv`.

### 5. Write GPU tests

Tests are standard pytest. The framework provides a `k8s_core_api` fixture (via the `conftest` plugin) and helpers for running GPU workloads as pods:

```python
# my_gpu_ci/tests/test_gpu.py
from accelerator_ci.testing.helpers import run_gpu_command

def test_gpu_detected(k8s_core_api):
    logs = run_gpu_command(
        k8s_core_api,
        "gpu-check",
        command=["my-gpu-tool", "--list"],
        gpu_resource_name="my-vendor.com/gpu",
        namespace="default",
        image="my-registry/gpu-test:latest",
    )
    assert "GPU 0" in logs
```

Tests can do anything — device enumeration, AI inference, training convergence. The framework runs pytest on whatever `get_test_path()` returns.

### 6. Run it

```bash
# Full cycle
accelerator-ci -c cluster-config.yaml deploy
accelerator-ci -c cluster-config.yaml --vendor-module my_gpu_ci.profile operators
accelerator-ci -c cluster-config.yaml --vendor-module my_gpu_ci.profile test-gpu --junit-xml results.xml
accelerator-ci -c cluster-config.yaml --vendor-module my_gpu_ci.profile cleanup
accelerator-ci -c cluster-config.yaml delete

# Or preview first
accelerator-ci -c cluster-config.yaml --vendor-module my_gpu_ci.profile --dry-run operators
```

### 7. Version pinning

Pin to a tag once accelerator-ci has stable releases:

```
accelerator-ci @ git+https://github.com/.../accelerator-ci.git@v1.0.0
```

## Makefile

For convenience, common commands are available as `make` targets:

```bash
make cluster-deploy   CONFIG_FILE_PATH=cluster-config.yaml
make cluster-delete   CONFIG_FILE_PATH=cluster-config.yaml
make cluster-status   CONFIG_FILE_PATH=cluster-config.yaml
make cluster-operators CONFIG_FILE_PATH=cluster-config.yaml VENDOR_MODULE=my_vendor.profile
make test-gpu          CONFIG_FILE_PATH=cluster-config.yaml VENDOR_MODULE=my_vendor.profile
make cluster-cleanup   CONFIG_FILE_PATH=cluster-config.yaml VENDOR_MODULE=my_vendor.profile
make must-gather       CONFIG_FILE_PATH=cluster-config.yaml
make test              # run unit + e2e tests
make help
```

Pass extra CLI flags via `EXTRA_FLAGS`:

```bash
make cluster-deploy CONFIG_FILE_PATH=config.yaml EXTRA_FLAGS='--dry-run --verbose'
```

## Architecture

The package is organized into four main areas:

- **`cluster_provision/`** — Cluster lifecycle: CLI entrypoint, Pydantic config validation, kcli deploy/delete (local and remote), status reporting.
- **`operators/`** — OLM operator installation: orchestration flow, subscription/CSV/InstallPlan primitives, cluster health polling, prerequisite checks.
- **`shared/`** — Cross-cutting utilities: `OcRunner` ABC (local subprocess and remote SSH), SSH/SCP with multiplexing, progress tracking, version comparison.
- **`vendors/`** — `VendorProfile` ABC and `OperatorSpec` dataclass. Vendor repos implement the profile; the CLI loads it dynamically via `--vendor-module`.
- **`testing/`** — Test infrastructure: pod lifecycle helpers, pytest runner (local and SSH tunnel), shared K8s client fixtures (registered as a pytest plugin).

### How it works

1. **Config** is loaded from YAML and validated with Pydantic. Missing required fields or wrong types produce clear multi-error messages.

2. **Deploy** uses kcli to create OpenShift clusters on libvirt (locally or on a remote host via SSH). For existing clusters, `--kubeconfig` skips this step.

3. **Operators** runs a vendor-agnostic orchestration flow: verify prerequisites, configure the internal registry, wait for cluster stability, run vendor pre-setup, install each operator via OLM subscriptions, run vendor post-setup, and wait for GPU readiness. Already-installed operators are skipped.

4. **Test** runs the vendor's pytest suite against the cluster, optionally through an SSH tunnel for remote clusters. Results can be written as JUnit XML.

5. **All oc commands** go through `OcRunner`, which provides automatic retry with exponential backoff for transient errors (connection refused, timeouts, 5xx, etc.).

## Troubleshooting

**"domain is still the placeholder 'example.com'"** — Change `domain` in your config to your actual cluster domain (e.g. `lab.local`).

**"pull_secret_path is required for deploy"** — Set `pull_secret_path` to your Red Hat pull secret file. Download from [console.redhat.com](https://console.redhat.com/openshift/install/pull-secret).

**"kubeconfig not found"** — For BYOC, verify the `--kubeconfig` path exists. For kcli clusters, ensure the cluster was deployed first.

**Operator install hangs** — Check `accelerator-ci -c config.yaml status` and `oc get csv -A`. Use `--verbose` for debug logs.
