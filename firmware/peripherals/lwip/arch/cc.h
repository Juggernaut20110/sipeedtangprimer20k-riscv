#ifndef TANG20K_LWIP_ARCH_CC_H
#define TANG20K_LWIP_ARCH_CC_H

#include <stdint.h>
#include <stdio.h>

#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#ifndef LITTLE_ENDIAN
#define LITTLE_ENDIAN 1234
#endif
#ifndef BIG_ENDIAN
#define BIG_ENDIAN 4321
#endif
#define BYTE_ORDER LITTLE_ENDIAN
#else
#error "lwIP port requires the configured little-endian RISC-V CPU"
#endif

#define PACK_STRUCT_FIELD(x) x
#define PACK_STRUCT_STRUCT __attribute__((packed))
#define PACK_STRUCT_BEGIN
#define PACK_STRUCT_END
#define LWIP_PLATFORM_DIAG(x) do { printf x; } while (0)
#define LWIP_PLATFORM_ASSERT(x) do { printf("lwIP assertion: %s\n", x); for (;;) {} } while (0)
#define LWIP_UNUSED_ARG(x) (void)(x)

#endif
