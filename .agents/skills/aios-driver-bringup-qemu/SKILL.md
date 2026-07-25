---
name: aios-driver-bringup-qemu
description: Use when working on AIOS e1000, storage, USB/xHCI, PCI probing, boot smoke tests, QEMU profiles, or device verification. Advance one observable bring-up checkpoint at a time and keep discovery, initialization, and data-path claims separate.
---

# AIOS Driver Bring-up and QEMU Runbook

Move a device path one honest step forward and make that step reproducible.

## Establish the current boundary

1. Inspect the driver, public device state, probe path, and existing QEMU
   profile.
2. Read the relevant design and `docs/tools/testkit_guide_ko.md`.
3. Identify whether the current path reaches discovery, resource mapping,
   initialization, interrupt setup, or actual data transfer.
4. Preserve existing profile behavior and unrelated device changes.

## Follow the bring-up ladder

Advance only the next missing rung:

1. device identity and capability discovery
2. BAR/MMIO/PIO or config-space access
3. reset and initialization state transition
4. descriptor, ring, queue, or buffer ownership
5. bounded polling or interrupt evidence
6. one smoke transfer with explicit completion evidence
7. cleanup, retry, and failure behavior

Do not widen several rungs at once unless their coupling is unavoidable.

## Produce verifier-grade evidence

Add one stable observable checkpoint such as:

- controller state with an explicit return or reason code
- queue/ring address, size, ownership, and ready state
- bounded TX/RX or read smoke result
- interrupt count or polled completion transition
- machine-readable `[STATE]` output

Keep human diagnostics separate from proof markers. A probe line is not proof of
an initialized controller, and initialization is not proof of a working data
path.

## Verify proportionally

1. Run host tests or static checks for pure logic first.
2. Run the narrow QEMU profile that contains the device.
3. Run `full`, `minimal`, and `storage-only` when shared boot markers, device
   expectations, or profile behavior changes.
4. Run the shell lane when state topics or clean exit behavior changes.
5. Preserve raw serial logs and report timeout or termination meaning.

Use `$aios-verification-tooling-guardian` when changing markers or verdicts and
`$aios-kernel-change-guardian` for kernel implementation.

## Report bounded support

Use exact phrases such as `probe only`, `init complete`, `TX smoke only`,
`bounded poll path`, or `interrupt wiring pending`. List the next unproven rung.
