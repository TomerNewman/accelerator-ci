CONFIG_FILE_PATH ?=
VENDOR_MODULE ?=
EXTRA_FLAGS ?=

CLI = accelerator-ci --config $(CONFIG_FILE_PATH) $(EXTRA_FLAGS)

cluster-deploy:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required. Usage: make cluster-deploy CONFIG_FILE_PATH=cluster-config.yaml)
endif
	$(CLI) deploy

cluster-delete:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required. Usage: make cluster-delete CONFIG_FILE_PATH=cluster-config.yaml)
endif
	$(CLI) delete

cluster-operators:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required)
endif
ifndef VENDOR_MODULE
	$(error VENDOR_MODULE is required. Usage: make cluster-operators CONFIG_FILE_PATH=... VENDOR_MODULE=my_vendor.profile)
endif
	$(CLI) --vendor-module $(VENDOR_MODULE) operators

test-gpu:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required)
endif
ifndef VENDOR_MODULE
	$(error VENDOR_MODULE is required. Usage: make test-gpu CONFIG_FILE_PATH=... VENDOR_MODULE=my_vendor.profile)
endif
	$(CLI) --vendor-module $(VENDOR_MODULE) test-gpu

cluster-cleanup:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required)
endif
ifndef VENDOR_MODULE
	$(error VENDOR_MODULE is required)
endif
	$(CLI) --vendor-module $(VENDOR_MODULE) cleanup

must-gather:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required. Usage: make must-gather CONFIG_FILE_PATH=cluster-config.yaml)
endif
	$(CLI) must-gather

cluster-status:
ifndef CONFIG_FILE_PATH
	$(error CONFIG_FILE_PATH is required. Usage: make cluster-status CONFIG_FILE_PATH=cluster-config.yaml)
endif
	$(CLI) status

test:
	python -m pytest tests/ -v

help:
	@echo "Accelerator CI - Multi-vendor GPU Operator CI for OpenShift"
	@echo ""
	@echo "Install: pip install -e .  (or pip install git+https://...)"
	@echo ""
	@echo "Cluster lifecycle (no vendor needed):"
	@echo "  make cluster-deploy  CONFIG_FILE_PATH=<path>"
	@echo "  make cluster-delete  CONFIG_FILE_PATH=<path>"
	@echo "  make cluster-status  CONFIG_FILE_PATH=<path>"
	@echo "  make must-gather     CONFIG_FILE_PATH=<path>"
	@echo ""
	@echo "Vendor operations (require VENDOR_MODULE):"
	@echo "  make cluster-operators CONFIG_FILE_PATH=<path> VENDOR_MODULE=<module>"
	@echo "  make test-gpu          CONFIG_FILE_PATH=<path> VENDOR_MODULE=<module>"
	@echo "  make cluster-cleanup   CONFIG_FILE_PATH=<path> VENDOR_MODULE=<module>"
	@echo ""
	@echo "Testing:"
	@echo "  make test"
	@echo ""
	@echo "Flags (pass via EXTRA_FLAGS):"
	@echo "  -n / --dry-run         Preview execution plan"
	@echo "  -v / --verbose         Debug logging"
	@echo "  --kubeconfig <path>    Use existing cluster (skip deploy/delete)"
	@echo "  --json-progress        Emit JSON progress for CI"
	@echo ""
	@echo "Examples:"
	@echo "  make cluster-deploy CONFIG_FILE_PATH=config.yaml"
	@echo "  make cluster-deploy CONFIG_FILE_PATH=config.yaml EXTRA_FLAGS='--dry-run'"
	@echo "  make cluster-operators CONFIG_FILE_PATH=config.yaml VENDOR_MODULE=my_vendor.profile"

.PHONY: cluster-deploy cluster-delete cluster-operators test-gpu cluster-cleanup must-gather cluster-status test help
