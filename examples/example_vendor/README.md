# Example Vendor

Demonstrates how to implement a `VendorProfile` for `accelerator-ci`.

Installs three operators in parallel with a dependency graph:

```
  NFD ──────┐
            ├──→ MetalLB
  NMState ──┘
```

NFD and NMState install concurrently. MetalLB waits for both.

No special hardware is required — works on any OpenShift cluster.

## Usage

```bash
# Install operators
accelerator-ci --config examples/example_vendor/config.yaml \
    --vendor-module examples.example_vendor.profile operators

# Check status
accelerator-ci --config examples/example_vendor/config.yaml status

# Run tests (requires a live cluster)
accelerator-ci --config examples/example_vendor/config.yaml \
    --vendor-module examples.example_vendor.profile test-gpu

# Cleanup
accelerator-ci --config examples/example_vendor/config.yaml \
    --vendor-module examples.example_vendor.profile cleanup
```

## Dry run

```bash
accelerator-ci --config examples/example_vendor/config.yaml \
    --vendor-module examples.example_vendor.profile --dry-run operators
```

## Use as a template

Copy this directory to start a new vendor profile:

```bash
cp -r examples/example_vendor my_vendor
# Edit my_vendor/profile.py — replace operators, CRs, and readiness checks
```
