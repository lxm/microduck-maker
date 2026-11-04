.DEFAULT_GOAL := help
PYTHON ?= python3
INPUTS ?= .build/inputs
BASE_IMAGE ?= $(INPUTS)/Armbian_26.8.1_Orangepizero3w_trixie_vendor_6.6.98_minimal.img
BOOT0 ?= $(INPUTS)/boot0-1gb.fex
MODULES ?= .build/audio
TOFD ?= .build/tofd-target/aarch64-unknown-linux-gnu/release/tofd
CAMERA ?= 1
OUTPUT ?= .build/image
KERNEL_HEADERS ?= .build/kernel/headers/linux-headers-6.6.98-vendor-sun60iw2
BASE_MODULES ?= .build/kernel/modules/6.6.98-vendor-sun60iw2

.PHONY: help upstream fetch kernel-inputs audio tofd image
help:
	@echo 'fetch / kernel-inputs: fetch pinned inputs / extract headers and base modules'
	@echo 'audio KERNEL_SOURCE=...: optional explicit audio compilation (native ARM64 Linux)'
	@echo 'tofd: compile the existing ToF crate; application releases use the existing V167 build'
	@echo 'image MODULES=... TOFD=...: build and verify IMG/XZ; no audio compilation'
	@echo 'See README.md for dependencies, application rebuilds and first login.'

upstream:
	sh scripts/upstream.sh

fetch:
	$(PYTHON) scripts/fetch_inputs.py --output "$(INPUTS)"

kernel-inputs:
	$(PYTHON) scripts/prepare_inputs.py --base-image "$(BASE_IMAGE)" --output .build/kernel

audio:
	@test -n "$(KERNEL_SOURCE)" || { echo 'Set KERNEL_SOURCE to the pinned upstream checkout'; exit 2; }
	sh scripts/build_modules.sh "$(KERNEL_SOURCE)" "$(KERNEL_HEADERS)" "$(BASE_MODULES)" "$(MODULES)"

tofd: upstream
	sh scripts/build_tofd.sh

image: upstream
ifeq ($(CAMERA),1)
	$(PYTHON) camera/fetch.py "$(INPUTS)/camera"
endif
	$(PYTHON) build_image.py --base-image "$(BASE_IMAGE)" --boot0 "$(BOOT0)" \
	  --modules "$(MODULES)" --tofd "$(TOFD)" --output "$(OUTPUT)" \
	  $(if $(APPLICATION),--application "$(APPLICATION)" --application-sha256 "$(APPLICATION_SHA256)") \
	  $(if $(WIFI_NETPLAN),--wifi-netplan "$(WIFI_NETPLAN)") \
	  $(if $(filter 1,$(CAMERA)),--camera-inputs "$(INPUTS)/camera" --reindex-modules,$(if $(filter 1,$(REINDEX_MODULES)),--reindex-modules))
