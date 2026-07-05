# Accelerator CI

Multi-vendor CI framework for testing GPU accelerator operators on OpenShift. Provisions clusters via kcli/libvirt, installs operator stacks through OLM, and runs GPU verification tests.

Supports **AMD**, **NVIDIA**, **Intel**, and any vendor that implements the `VendorProfile` interface.

## Installation

```bash
pip install git+https://github.com/rh-ecosystem-edge/accelerator-ci.git@main

# For development on accelerator-ci itself
git clone https://github.com/rh-ecosystem-edge/accelerator-ci.git
cd accelerator-ci
pip install -e .
```

## Usage

After installation, the `accelerator-ci` CLI is available:

```bash
# Cluster lifecycle (no vendor profile needed)
accelerator-ci --config cluster-config.yaml deploy
accelerator-ci --config cluster-config.yaml delete
accelerator-ci --config cluster-config.yaml must-gather

# Vendor operations (require --vendor-module)
accelerator-ci --config cluster-config.yaml --vendor-module amd_ci.profile operators
accelerator-ci --config cluster-config.yaml --vendor-module amd_ci.profile test-gpu
accelerator-ci --config cluster-config.yaml --vendor-module amd_ci.profile cleanup
```

## Workflow

```
cluster-deploy → cluster-operators → test-gpu → cluster-cleanup → cluster-delete
```

| Command | Description | Vendor needed? |
|---------|-------------|----------------|
| `deploy` | Provision an OpenShift cluster (local or remote libvirt) | No |
| `delete` | Destroy the cluster | No |
| `operators` | Install the GPU operator stack via OLM | Yes |
| `test-gpu` | Run GPU verification tests | Yes |
| `cleanup` | Remove the operator stack | Yes |
| `must-gather` | Collect diagnostic data | No |

## Creating a Vendor Repo

Step-by-step guide for building a new vendor CI repo (e.g. `amd-ci`, `nvidia-ci`).

### 1. Set up the repo

```bash
mkdir my-vendor-ci && cd my-vendor-ci
python3 -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/rh-ecosystem-edge/accelerator-ci.git@main
```

Add to `requirements.txt`:

```
accelerator-ci @ git+https://github.com/rh-ecosystem-edge/accelerator-ci.git@main
```

### 2. Create your vendor profile

```
my-vendor-ci/
├── my_vendor_ci/
│   ├── __init__.py
│   ├── profile.py
│   └── tests/
│       └── test_gpu.py
├── cluster-config.yaml
├── requirements.txt
└── Makefile
```

Implement `VendorProfile` in `my_vendor_ci/profile.py`:

```python
from accelerator_ci.vendors.base import VendorProfile, OperatorSpec

class VendorProfile(VendorProfile):
    @property
    def display_name(self) -> str:
        return "My GPU Operator"

    def get_operators(self, vendor_config):
        return [
            OperatorSpec(name="my-gpu-operator", package="my-gpu-operator",
                         namespace="my-gpu-system", catalog="certified-operators",
                         channel="stable"),
        ]

    def pre_operator_setup(self, oc, vendor_config, machine_config_role):
        # e.g. blacklist in-tree driver via MachineConfig
        pass

    def post_operator_setup(self, oc, vendor_config, ocp_version):
        # e.g. create NFD rules, DeviceConfig, ClusterPolicy
        pass

    def wait_for_gpu_ready(self, oc, timeout=900):
        # poll until nodes report your GPU extended resource
        pass

    def cleanup(self, oc):
        # delete CRs, subscriptions, namespaces
        pass

    def get_test_path(self) -> str:
        return "my_vendor_ci/tests"
```

| Method | Purpose |
|--------|---------|
| `display_name` | Human-readable name for logging |
| `get_operators()` | Ordered list of OLM operators to install |
| `pre_operator_setup()` | Pre-install steps (e.g. driver blacklist MachineConfig) |
| `post_operator_setup()` | Post-install steps (e.g. NFD rules, vendor CRs) |
| `wait_for_gpu_ready()` | Poll until GPU resources appear on nodes |
| `cleanup()` | Reverse the installation |
| `get_test_path()` | Path to vendor-specific pytest tests |

### 3. Write your tests

Tests are just pytest. Use the provided helpers or write your own:

```python
# my_vendor_ci/tests/test_gpu.py
from accelerator_ci.testing.helpers import run_gpu_command

def test_gpu_detected(k8s_core_api):
    logs = run_gpu_command(
        k8s_core_api, "gpu-check",
        command=["my-gpu-tool", "--list"],
        gpu_resource_name="my-vendor.com/gpu",
        namespace="default",
        image="my-registry/gpu-test:latest",
    )
    assert "GPU 0" in logs
```

Tests can do anything -- basic verification, AI inference workloads, training convergence checks. The framework runs pytest on whatever directory `get_test_path()` returns.

### 4. Create your config and run

Copy `cluster-config.yaml.example` from this repo, add vendor-specific fields under `operators:`, then:

```bash
accelerator-ci --config cluster-config.yaml deploy
accelerator-ci --config cluster-config.yaml --vendor-module my_vendor_ci.profile operators
accelerator-ci --config cluster-config.yaml --vendor-module my_vendor_ci.profile test-gpu
accelerator-ci --config cluster-config.yaml delete
```

### 5. Version pinning

Once accelerator-ci has stable releases, pin to a tag instead of `main`:

```
accelerator-ci @ git+https://github.com/.../accelerator-ci.git@v1.0.0
```

## Package Structure

```
accelerator_ci/
├── cluster_provision/     Cluster lifecycle (deploy, delete, must-gather)
│   └── main.py            CLI entrypoint
├── operators/             Generic OLM installation framework
│   ├── orchestrator.py    Install flow using VendorProfile
│   ├── install.py         OLM primitives (subscriptions, CSVs, CRDs)
│   └── cluster_health.py  Node readiness and MCP health checks
├── vendors/
│   └── base.py            VendorProfile ABC + OperatorSpec
├── shared/
│   ├── oc_runner.py       OcRunner (local + SSH remote)
│   └── ssh.py             SSH/SCP with multiplexing
└── testing/               Test infrastructure
    ├── helpers.py          Pod lifecycle + GPU workload helpers
    └── runner.py           pytest runner (local + SSH tunnel)
```

## Configuration

See [`cluster-config.yaml.example`](cluster-config.yaml.example) for all fields. Key settings:

- `ocp_version` — OpenShift version (e.g. `"4.20"`, auto-resolves to latest patch)
- `operators.install` — set `true` to install operators during `cluster-operators`
- `operators.machine_config_role` — `"worker"` normally, `"master"` for SNO
- `remote.host` — set to deploy on a remote libvirt host (null = local)

Vendor-specific fields (GPU operator version, driver version, etc.) go under `operators:` and are passed to the vendor profile as a dict.
