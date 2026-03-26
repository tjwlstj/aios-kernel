/*
 * AIOS Kernel - Intel E1000 Minimal Driver
 * AI-Native Operating System
 */

#ifndef _AIOS_E1000_H
#define _AIOS_E1000_H

#include <kernel/types.h>

aios_status_t e1000_driver_init(void);
bool e1000_driver_ready(void);
void e1000_driver_dump(void);

#endif /* _AIOS_E1000_H */
