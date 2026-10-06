#include "lwip_sys.h"

#include <lwip/sys.h>

static volatile u32_t current_ms;

void lwip_sys_set_ms(uint32_t value)
{
    current_ms = value;
}

u32_t sys_now(void)
{
    return current_ms;
}
