/*
 * AIOS Kernel - PS/2 Keyboard Driver
 * AI-Native Operating System
 *
 * Handles PS/2 keyboard input via IRQ1 (PIC vector 33).
 * Provides a ring-buffered character input API used by the kernel shell.
 */

#ifndef _AIOS_DRIVERS_KEYBOARD_H
#define _AIOS_DRIVERS_KEYBOARD_H

#include <kernel/types.h>

/* PIC vector for keyboard IRQ1 (remapped from hardware IRQ 1) */
#define KEYBOARD_IRQ_VECTOR  33U

/*
 * Initialize the PS/2 keyboard driver.
 * Unmasks IRQ1 in the PIC and resets the ring buffer.
 */
aios_status_t keyboard_init(void);

/*
 * Called from the interrupt dispatcher (idt.c) on every IRQ1 firing.
 * Reads the scancode from port 0x60 and pushes the decoded character
 * into the ring buffer.
 */
void keyboard_irq_handler(void);

/* Returns true if at least one character is waiting in the ring buffer. */
bool keyboard_haschar(void);

/*
 * Blocking read — halts the CPU (via HLT) until a character is available,
 * then returns and removes it from the ring buffer.
 * Interrupts must be enabled by the caller.
 */
char keyboard_getchar(void);

#endif /* _AIOS_DRIVERS_KEYBOARD_H */
