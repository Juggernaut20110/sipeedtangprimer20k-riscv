#include "sd_protocol.h"

#include <limits.h>

int sd_protocol_divider(uint32_t sys_hz, uint32_t target_hz, uint16_t *divider)
{
    uint64_t value;

    if (sys_hz == 0 || target_hz == 0 || divider == NULL)
        return 0;
    value = ((uint64_t)sys_hz + target_hz - 1u) / target_hz;
    if (value < 2u)
        value = 2u;
    if (value > UINT16_MAX)
        return 0;
    *divider = (uint16_t)value;
    return 1;
}

uint32_t sd_protocol_actual_hz(uint32_t sys_hz, uint16_t divider)
{
    if (sys_hz == 0 || divider < 2u)
        return 0;
    return sys_hz / divider;
}

int sd_protocol_lba_argument(uint64_t lba, int block_addressed, uint32_t *argument)
{
    uint64_t value = lba;

    if (argument == NULL)
        return 0;
    if (!block_addressed) {
        if (lba > UINT32_MAX / 512u)
            return 0;
        value = lba * 512u;
    }
    if (value > UINT32_MAX)
        return 0;
    *argument = (uint32_t)value;
    return 1;
}

int sd_protocol_range_valid(uint64_t total_sectors, uint64_t first, uint32_t count)
{
    if (count == 0 || first >= total_sectors)
        return 0;
    return (uint64_t)count <= total_sectors - first;
}

int sd_protocol_csd_sectors(const uint8_t csd[16], uint64_t *sectors)
{
    uint64_t bytes;
    uint32_t c_size;
    unsigned int read_bl_len;
    unsigned int c_size_mult;

    if (csd == NULL || sectors == NULL)
        return 0;

    switch (csd[0] >> 6) {
    case 1: /* CSD v2: SDHC/SDXC block-addressed capacity. */
        c_size = ((uint32_t)(csd[7] & 0x3fu) << 16)
               | ((uint32_t)csd[8] << 8)
               | (uint32_t)csd[9];
        *sectors = ((uint64_t)c_size + 1u) * 1024u;
        return *sectors != 0;

    case 0: /* CSD v1: SDSC byte-addressed capacity. */
        read_bl_len = csd[5] & 0x0fu;
        c_size = ((uint32_t)(csd[6] & 0x03u) << 10)
               | ((uint32_t)csd[7] << 2)
               | ((uint32_t)(csd[8] & 0xc0u) >> 6);
        c_size_mult = ((unsigned int)(csd[9] & 0x03u) << 1)
                    | ((unsigned int)(csd[10] & 0x80u) >> 7);
        if (read_bl_len > 31u || c_size_mult > 7u)
            return 0;
        bytes = ((uint64_t)c_size + 1u) * ((uint64_t)1u << (c_size_mult + 2u));
        if (bytes > (UINT64_MAX >> read_bl_len))
            return 0;
        bytes <<= read_bl_len;
        if (bytes < 512u || (bytes & 511u) != 0)
            return 0;
        *sectors = bytes / 512u;
        return *sectors != 0;

    default:
        return 0;
    }
}
