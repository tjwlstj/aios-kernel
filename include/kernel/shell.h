/*
 * AIOS Kernel - Interactive Kernel Shell
 * AI-Native Operating System
 *
 * A minimal REPL that gives the user an interactive prompt after boot.
 * Input comes from the PS/2 keyboard driver; output goes to the VGA console.
 */

#ifndef _AIOS_KERNEL_SHELL_H
#define _AIOS_KERNEL_SHELL_H

#include <kernel/types.h>

/* Prepare shell state (buffer, color). Must be called before shell_run(). */
aios_status_t shell_init(void);

/*
 * Enter the interactive shell loop.
 * This function never returns — it waits for keyboard input and processes
 * commands until a 'reboot' command is issued.
 */
void shell_run(void);

#endif /* _AIOS_KERNEL_SHELL_H */
