/*
 * AIOS Kernel - String/Memory Utility Functions
 * AI-Native Operating System
 */

#ifndef _AIOS_STRING_H
#define _AIOS_STRING_H

#include <kernel/types.h>

/* Memory operations */
void *memset(void *dest, int val, size_t count);
void *memcpy(void *dest, const void *src, size_t count);
void *memmove(void *dest, const void *src, size_t count);
int   memcmp(const void *s1, const void *s2, size_t count);

/* String operations */
size_t strlen(const char *str);
int    strcmp(const char *s1, const char *s2);
int    strncmp(const char *s1, const char *s2, size_t n);
char  *strcpy(char *dest, const char *src);
char  *strncpy(char *dest, const char *src, size_t n);

#endif /* _AIOS_STRING_H */
