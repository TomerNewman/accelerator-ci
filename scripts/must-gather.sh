#!/bin/bash
# Must-gather diagnostic collection script.
#
# This script collects diagnostic data from the cluster. It is designed
# to be vendor-agnostic and collect common OpenShift diagnostics.
# Vendor-specific collection can be added via environment variables or
# additional scripts.
#
# Required environment variables:
#   KUBECONFIG   - Path to kubeconfig file
#   ARTIFACT_DIR - Directory to store collected data
#
# Optional:
#   VENDOR_NAMESPACES - Comma-separated list of vendor namespaces to inspect

set -euo pipefail

ARTIFACT_DIR="${ARTIFACT_DIR:-./must-gather-output}"
mkdir -p "$ARTIFACT_DIR"

echo "=== Must-Gather Diagnostic Collection ==="
echo "KUBECONFIG: ${KUBECONFIG:-not set}"
echo "ARTIFACT_DIR: $ARTIFACT_DIR"
echo ""

# Cluster info
echo "--- Collecting cluster info ---"
oc get clusterversion -o yaml > "$ARTIFACT_DIR/clusterversion.yaml" 2>/dev/null || true
oc get nodes -o wide > "$ARTIFACT_DIR/nodes.txt" 2>/dev/null || true
oc get co > "$ARTIFACT_DIR/clusteroperators.txt" 2>/dev/null || true
oc get mcp > "$ARTIFACT_DIR/machineconfigpools.txt" 2>/dev/null || true

# Collect from vendor-specific namespaces if provided
if [ -n "${VENDOR_NAMESPACES:-}" ]; then
    IFS=',' read -ra NAMESPACES <<< "$VENDOR_NAMESPACES"
    for ns in "${NAMESPACES[@]}"; do
        ns=$(echo "$ns" | xargs)  # trim whitespace
        echo "--- Collecting from namespace: $ns ---"
        ns_dir="$ARTIFACT_DIR/$ns"
        mkdir -p "$ns_dir"

        oc get pods -n "$ns" -o wide > "$ns_dir/pods.txt" 2>/dev/null || true
        oc get events -n "$ns" --sort-by='.lastTimestamp' > "$ns_dir/events.txt" 2>/dev/null || true

        # Collect logs from all pods in the namespace
        for pod in $(oc get pods -n "$ns" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
            oc logs "$pod" -n "$ns" --all-containers > "$ns_dir/${pod}.log" 2>/dev/null || true
        done
    done
fi

# NFD (common across vendors)
echo "--- Collecting NFD data ---"
nfd_dir="$ARTIFACT_DIR/nfd"
mkdir -p "$nfd_dir"
oc get pods -n openshift-nfd -o wide > "$nfd_dir/pods.txt" 2>/dev/null || true
oc get nodefeaturediscoveries -A -o yaml > "$nfd_dir/nodefeaturediscoveries.yaml" 2>/dev/null || true
oc get nodefeaturerules -A -o yaml > "$nfd_dir/nodefeaturerules.yaml" 2>/dev/null || true

# Node labels (GPU-relevant)
echo "--- Collecting node labels ---"
oc get nodes -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for node in data.get('items', []):
    name = node['metadata']['name']
    labels = {k: v for k, v in node['metadata'].get('labels', {}).items()
              if any(x in k for x in ['gpu', 'accelerator', 'feature.node', 'amd', 'nvidia', 'intel'])}
    if labels:
        print(f'{name}:')
        for k, v in sorted(labels.items()):
            print(f'  {k}={v}')
" > "$ARTIFACT_DIR/gpu-labels.txt" 2>/dev/null || true

echo ""
echo "=== Must-gather complete. Results in: $ARTIFACT_DIR ==="
