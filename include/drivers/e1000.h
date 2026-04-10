/*
 * AIOS Kernel - Intel E1000 Minimal Driver
 * AI-Native Operating System
 */

#ifndef _AIOS_E1000_H
#define _AIOS_E1000_H

#include <kernel/types.h>

typedef struct {
    bool present;
    bool ready;
    bool link_up;
    bool has_eeprom;
    bool rx_ready;
    bool tx_ready;
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    uint16_t vendor_id;
    uint16_t device_id;
    uint16_t io_base;
    uint64_t mmio_base;
    uint8_t mac[6];
    uint32_t status;
    aios_status_t last_rx_status;
    aios_status_t last_tx_status;
    uint16_t last_rx_length;
    uint32_t rx_poll_count;
    uint32_t rx_frame_count;
    uint32_t rx_drop_count;
} e1000_driver_info_t;

aios_status_t e1000_driver_init(void);
bool e1000_driver_ready(void);
aios_status_t e1000_driver_rx_poll(uint16_t *out_length);
aios_status_t e1000_driver_tx_smoke(void);
aios_status_t e1000_driver_info(e1000_driver_info_t *out);
void e1000_driver_dump(void);

#endif /* _AIOS_E1000_H */
