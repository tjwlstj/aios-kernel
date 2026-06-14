# =============================================================================
# AIOS - Top-level build orchestration (monorepo)
# -----------------------------------------------------------------------------
# This is a thin delegator. Each concern lives in its own domain:
#   kernel/   bare-metal kernel build  (real Makefile is kernel/Makefile)
#   tools/    test tooling + orchestration (testkit)
# See PROJECT.md for the full domain map and boundary rules.
# =============================================================================

KERNEL_DIR := kernel
TESTKIT    := tools/testkit/aios-testkit.py
PYTHON     ?= python

.PHONY: all iso run run-headless debug test clean info kernel os-smoke testkit help

# --- Kernel domain (delegated to kernel/Makefile) ---------------------------
all iso run run-headless debug test clean info:
	$(MAKE) -C $(KERNEL_DIR) $@

kernel:
	$(MAKE) -C $(KERNEL_DIR) all

# --- Test tooling domain (tools/) -------------------------------------------
os-smoke:
	$(PYTHON) $(TESTKIT) os

testkit:
	$(PYTHON) $(TESTKIT) all --strict

help:
	@echo "AIOS monorepo targets:"
	@echo "  make all | iso | run | run-headless | debug | test | clean | info"
	@echo "        -> bare-metal kernel build (delegated to kernel/)"
	@echo "  make os-smoke   -> OS tool smoke test (tools/testkit)"
	@echo "  make testkit    -> full testkit suite (tools/testkit)"
	@echo ""
	@echo "Domain map and boundary rules: see PROJECT.md"
