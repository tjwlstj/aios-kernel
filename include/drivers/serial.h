/*
 * AIOS Kernel - Serial Console Driver (COM1)
 * AI-Native Operating System
 *
 * Provides serial output for headless/QEMU debugging.
 * Default: COM1 (0x3F8), 115200 baud, 8N1
 */

#ifndef _AIOS_SERIAL_H
#define _AIOS_SERIAL_H

#include <kernel/types.h>

#define SERIAL_COM1_BASE    0x3F8
#define SERIAL_COM2_BASE    0x2F8

/* Serial port registers (offsets from base) */
#define SERIAL_DATA         0   /* Data register (R/W) */
#define SERIAL_IER          1   /* Interrupt Enable Register */
#define SERIAL_FIFO         2   /* FIFO Control Register */
#define SERIAL_LCR          3   /* Line Control Register */
#define SERIAL_MCR          4   /* Modem Control Register */
#define SERIAL_LSR          5   /* Line Status Register */

/* Line Status Register bits */
#define SERIAL_LSR_DR       0x01  /* Data Ready */
#define SERIAL_LSR_THRE     0x20  /* Transmitter Holding Register Empty */

/* Initialize serial port */
aios_status_t serial_init(void);

/* Write a single character to serial */
void serial_putchar(char c);

/* Write a string to serial */
void serial_write(const char *str);

/* Write formatted output to serial (mirrors kprintf) */
void serial_printf(const char *fmt, ...);

/* Check if serial data is available */
bool serial_data_ready(void);

/* Read a character from serial (blocking) */
char serial_getchar(void);

#endif /* _AIOS_SERIAL_H */
