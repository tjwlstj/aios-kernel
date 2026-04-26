/*
 * AIOS Kernel - PS/2 Keyboard Driver
 * AI-Native Operating System
 *
 * Implements IRQ1-driven PS/2 keyboard input using scancode set 1
 * (the default used by IBM PC-compatible BIOS and QEMU).
 *
 * Key features:
 *   - US QWERTY scancode → ASCII translation (normal + shift)
 *   - Left/right Shift, Ctrl tracking
 *   - 256-character ring buffer (lock-free single-producer/single-consumer)
 *   - Ctrl+C emits ASCII ETX (0x03) for shell interrupt
 *   - Backspace (\b), Tab (\t), Enter (\n) passed through as-is
 */

#include <drivers/keyboard.h>

/* -------------------------------------------------------------------------
 * Hardware constants
 * ---------------------------------------------------------------------- */

#define KBD_DATA_PORT    0x60   /* Read scancode / write command data     */
#define KBD_STATUS_PORT  0x64   /* Read status                            */
#define PIC1_DATA_PORT   0x21   /* PIC master IMR (interrupt mask register) */

/* Specific scancodes (set 1) */
#define SC_LSHIFT   0x2A
#define SC_RSHIFT   0x36
#define SC_LCTRL    0x1D
#define SC_LALT     0x38
#define SC_CAPSLOCK 0x3A
#define SC_RELEASE  0x80   /* OR-mask: key-release events have bit 7 set */

/* -------------------------------------------------------------------------
 * Scancode → ASCII tables (US QWERTY, set 1, 128 entries each)
 *
 * Index = scancode byte (0x00..0x7F).
 * 0 = no printable character (modifier / function key / reserved).
 * ---------------------------------------------------------------------- */

static const char sc_normal[128] = {
/* 00 */  0,    27,   '1',  '2',  '3',  '4',  '5',  '6',
/* 08 */  '7',  '8',  '9',  '0',  '-',  '=',  '\b', '\t',
/* 10 */  'q',  'w',  'e',  'r',  't',  'y',  'u',  'i',
/* 18 */  'o',  'p',  '[',  ']',  '\n', 0,    'a',  's',
/* 20 */  'd',  'f',  'g',  'h',  'j',  'k',  'l',  ';',
/* 28 */  '\'', '`',  0,    '\\', 'z',  'x',  'c',  'v',
/* 30 */  'b',  'n',  'm',  ',',  '.',  '/',  0,    '*',
/* 38 */  0,    ' ',  0,    0,    0,    0,    0,    0,
/* 40 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 48 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 50 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 58 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 60 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 68 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 70 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 78 */  0,    0,    0,    0,    0,    0,    0,    0,
};

static const char sc_shift[128] = {
/* 00 */  0,    27,   '!',  '@',  '#',  '$',  '%',  '^',
/* 08 */  '&',  '*',  '(',  ')',  '_',  '+',  '\b', '\t',
/* 10 */  'Q',  'W',  'E',  'R',  'T',  'Y',  'U',  'I',
/* 18 */  'O',  'P',  '{',  '}',  '\n', 0,    'A',  'S',
/* 20 */  'D',  'F',  'G',  'H',  'J',  'K',  'L',  ':',
/* 28 */  '"',  '~',  0,    '|',  'Z',  'X',  'C',  'V',
/* 30 */  'B',  'N',  'M',  '<',  '>',  '?',  0,    '*',
/* 38 */  0,    ' ',  0,    0,    0,    0,    0,    0,
/* 40 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 48 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 50 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 58 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 60 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 68 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 70 */  0,    0,    0,    0,    0,    0,    0,    0,
/* 78 */  0,    0,    0,    0,    0,    0,    0,    0,
};

/* -------------------------------------------------------------------------
 * Ring buffer (256 slots — power of 2 simplifies wrap-around masking)
 * ---------------------------------------------------------------------- */

#define KBD_BUF_SIZE  256U
#define KBD_BUF_MASK  (KBD_BUF_SIZE - 1U)

static volatile char     kbd_buf[KBD_BUF_SIZE];
static volatile uint32_t kbd_rd = 0;   /* consumer index */
static volatile uint32_t kbd_wr = 0;   /* producer index (IRQ side) */

/* -------------------------------------------------------------------------
 * Modifier state (updated by the IRQ handler)
 * ---------------------------------------------------------------------- */

static volatile bool kbd_shift = false;
static volatile bool kbd_ctrl  = false;
static bool          kbd_ready = false;

/* -------------------------------------------------------------------------
 * I/O helpers (file-local; avoid duplicate symbol with other drivers)
 * ---------------------------------------------------------------------- */

static inline uint8_t kbd_inb(uint16_t port) {
    uint8_t v;
    __asm__ volatile ("inb %1, %0" : "=a"(v) : "Nd"(port));
    return v;
}

static inline void kbd_outb(uint16_t port, uint8_t val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));
}

/* -------------------------------------------------------------------------
 * Ring buffer helpers
 * ---------------------------------------------------------------------- */

static inline bool kbd_buf_full(void) {
    return ((kbd_wr + 1U) & KBD_BUF_MASK) == (kbd_rd & KBD_BUF_MASK);
}

static inline void kbd_buf_push(char c) {
    if (!kbd_buf_full()) {
        kbd_buf[kbd_wr & KBD_BUF_MASK] = c;
        kbd_wr = (kbd_wr + 1U) & (KBD_BUF_SIZE * 2U - 1U); /* never wraps past 2x */
    }
    /* Silently drop if full — avoids blocking in an IRQ handler */
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

aios_status_t keyboard_init(void) {
    kbd_rd    = 0;
    kbd_wr    = 0;
    kbd_shift = false;
    kbd_ctrl  = false;

    /* Unmask IRQ1 (keyboard) in the PIC master IMR.
     * timer.c only unmasked IRQ0 (bit 0). We clear bit 1 here. */
    uint8_t mask = kbd_inb(PIC1_DATA_PORT);
    mask &= (uint8_t)~(1U << 1);
    kbd_outb(PIC1_DATA_PORT, mask);

    kbd_ready = true;
    return AIOS_OK;
}

void keyboard_irq_handler(void) {
    uint8_t sc = kbd_inb(KBD_DATA_PORT);

    /* Key-release event: clear modifier state, ignore everything else */
    if (sc & SC_RELEASE) {
        uint8_t released = sc & (uint8_t)~SC_RELEASE;
        if (released == SC_LSHIFT || released == SC_RSHIFT) kbd_shift = false;
        if (released == SC_LCTRL)                            kbd_ctrl  = false;
        return;
    }

    /* Modifier key press */
    if (sc == SC_LSHIFT || sc == SC_RSHIFT) { kbd_shift = true;  return; }
    if (sc == SC_LCTRL)                     { kbd_ctrl  = true;  return; }
    if (sc == SC_LALT || sc == SC_CAPSLOCK) { return; }

    if (sc >= 128) return; /* extended / unknown scancode */

    char ascii = kbd_shift ? sc_shift[sc] : sc_normal[sc];
    if (ascii == 0) return; /* non-printable function key */

    /* Ctrl+C → ETX (0x03) */
    if (kbd_ctrl && (ascii == 'c' || ascii == 'C')) {
        kbd_buf_push('\x03');
        return;
    }

    kbd_buf_push(ascii);
}

bool keyboard_haschar(void) {
    return (kbd_rd & KBD_BUF_MASK) != (kbd_wr & KBD_BUF_MASK);
}

char keyboard_getchar(void) {
    /* Busy-wait with HLT to yield to interrupts */
    while (!keyboard_haschar()) {
        __asm__ volatile ("hlt" ::: "memory");
    }
    char c = kbd_buf[kbd_rd & KBD_BUF_MASK];
    kbd_rd = (kbd_rd + 1U) & (KBD_BUF_SIZE * 2U - 1U);
    return c;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
