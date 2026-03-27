/*
 * AIOS Kernel - Minimal USB Host Bootstrap
 * AI-Native Operating System
 */

#ifndef _AIOS_USB_HOST_H
#define _AIOS_USB_HOST_H

#include <kernel/types.h>

typedef enum {
    USB_HOST_CONTROLLER_NONE = 0,
    USB_HOST_CONTROLLER_UHCI = 1,
    USB_HOST_CONTROLLER_OHCI = 2,
    USB_HOST_CONTROLLER_EHCI = 3,
    USB_HOST_CONTROLLER_XHCI = 4,
} usb_host_controller_kind_t;

typedef struct {
    bool present;
    bool ready;
    usb_host_controller_kind_t controller_kind;
    uint8_t prog_if;
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    uint16_t vendor_id;
    uint16_t device_id;
    uint16_t io_base;
    uint64_t mmio_base;
    uint32_t pci_command;
    aios_status_t last_init_status;
} usb_host_info_t;

aios_status_t usb_host_init(void);
bool usb_host_ready(void);
aios_status_t usb_host_info(usb_host_info_t *out);
void usb_host_dump(void);

#endif /* _AIOS_USB_HOST_H */
