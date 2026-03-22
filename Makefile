# =============================================================================
# AIOS Kernel - Build System
# AI-Native Operating System
# =============================================================================

# Toolchain
TOOLCHAIN_PREFIX ?=
ASM         ?= nasm
CC          ?= $(TOOLCHAIN_PREFIX)gcc
LD          ?= $(TOOLCHAIN_PREFIX)ld
OBJCOPY     ?= $(TOOLCHAIN_PREFIX)objcopy

# Directories
BOOT_DIR    = boot
KERNEL_DIR  = kernel
MM_DIR      = mm
SCHED_DIR   = sched
HAL_DIR     = hal
RUNTIME_DIR = runtime
DRIVERS_DIR = drivers
INCLUDE_DIR = include
BUILD_DIR   = build
ISO_DIR     = $(BUILD_DIR)/isofiles

# Output
KERNEL_BIN  = $(BUILD_DIR)/aios-kernel.bin
KERNEL_ISO  = $(BUILD_DIR)/aios-kernel.iso

# Flags
ASMFLAGS    = -f elf64
CFLAGS      = -ffreestanding -nostdlib -nostdinc -fno-builtin -fno-stack-protector \
              -mno-red-zone -mno-mmx -mno-sse -mno-sse2 \
              -Wall -Wextra -Wno-unused-parameter -O2 \
              -I$(INCLUDE_DIR) \
              -mcmodel=kernel -fno-pic -fno-pie
LDFLAGS     = -T $(KERNEL_DIR)/linker.ld -nostdlib -z max-page-size=0x1000

# Source files
ASM_SOURCES = $(BOOT_DIR)/boot.asm
C_SOURCES   = $(KERNEL_DIR)/main.c \
              $(MM_DIR)/tensor_mm.c \
              $(SCHED_DIR)/ai_sched.c \
              $(HAL_DIR)/accel_hal.c \
              $(RUNTIME_DIR)/ai_syscall.c \
              $(DRIVERS_DIR)/vga.c

# Object files
ASM_OBJECTS = $(patsubst %.asm,$(BUILD_DIR)/%.o,$(ASM_SOURCES))
C_OBJECTS   = $(patsubst %.c,$(BUILD_DIR)/%.o,$(C_SOURCES))
OBJECTS     = $(ASM_OBJECTS) $(C_OBJECTS)

# =============================================================================
# Targets
# =============================================================================

.PHONY: all clean iso run debug check

all: check $(KERNEL_BIN)

check:
	@echo "[CHECK] Validating build toolchain..."
	@command -v $(ASM) >/dev/null 2>&1 || { echo "[ERR] Missing assembler: $(ASM)"; exit 1; }
	@command -v $(CC) >/dev/null 2>&1 || { echo "[ERR] Missing compiler: $(CC)"; exit 1; }
	@command -v $(LD) >/dev/null 2>&1 || { echo "[ERR] Missing linker: $(LD)"; exit 1; }
	@echo "[OK] ASM: $$($(ASM) --version | head -n 1)"
	@echo "[OK]  CC: $$($(CC) --version | head -n 1)"
	@echo "[OK]  LD: $$($(LD) --version | head -n 1)"

# Build kernel binary
$(KERNEL_BIN): $(OBJECTS) $(KERNEL_DIR)/linker.ld
	@echo "[LD] Linking kernel..."
	@mkdir -p $(dir $@)
	$(LD) $(LDFLAGS) -o $@ $(OBJECTS)
	@echo "[OK] Kernel binary: $@"
	@echo "[INFO] Kernel size: $$(wc -c < $@) bytes"

# Assemble .asm files
$(BUILD_DIR)/%.o: %.asm
	@echo "[ASM] $<"
	@mkdir -p $(dir $@)
	$(ASM) $(ASMFLAGS) $< -o $@

# Compile .c files
$(BUILD_DIR)/%.o: %.c
	@echo "[CC] $<"
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

# Create bootable ISO
iso: $(KERNEL_BIN)
	@echo "[ISO] Creating bootable ISO..."
	@mkdir -p $(ISO_DIR)/boot/grub
	@cp $(KERNEL_BIN) $(ISO_DIR)/boot/aios-kernel.bin
	@echo 'set timeout=3' > $(ISO_DIR)/boot/grub/grub.cfg
	@echo 'set default=0' >> $(ISO_DIR)/boot/grub/grub.cfg
	@echo '' >> $(ISO_DIR)/boot/grub/grub.cfg
	@echo 'menuentry "AIOS - AI-Native Operating System" {' >> $(ISO_DIR)/boot/grub/grub.cfg
	@echo '    multiboot2 /boot/aios-kernel.bin' >> $(ISO_DIR)/boot/grub/grub.cfg
	@echo '    boot' >> $(ISO_DIR)/boot/grub/grub.cfg
	@echo '}' >> $(ISO_DIR)/boot/grub/grub.cfg
	grub-mkrescue -o $(KERNEL_ISO) $(ISO_DIR)
	@echo "[OK] Bootable ISO: $(KERNEL_ISO)"

# Run in QEMU
run: $(KERNEL_BIN)
	qemu-system-x86_64 \
		-kernel $(KERNEL_BIN) \
		-m 2G \
		-serial stdio \
		-display curses \
		-no-reboot \
		-no-shutdown

# Run in QEMU with debug
debug: $(KERNEL_BIN)
	qemu-system-x86_64 \
		-kernel $(KERNEL_BIN) \
		-m 2G \
		-serial stdio \
		-display curses \
		-no-reboot \
		-no-shutdown \
		-s -S

# Clean build artifacts
clean:
	@echo "[CLEAN] Removing build directory..."
	rm -rf $(BUILD_DIR)
	@echo "[OK] Clean complete"

# Print build info
info:
	@echo "=== AIOS Kernel Build Info ==="
	@echo "ASM Sources: $(ASM_SOURCES)"
	@echo "C Sources:   $(C_SOURCES)"
	@echo "Objects:     $(OBJECTS)"
	@echo "Kernel:      $(KERNEL_BIN)"
	@echo "ISO:         $(KERNEL_ISO)"
	@echo "=============================="
