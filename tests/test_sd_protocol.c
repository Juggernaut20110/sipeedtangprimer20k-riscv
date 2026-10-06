#include "../firmware/peripherals/sd_protocol.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
    uint8_t csd[16] = {0};
    uint16_t divider;
    uint32_t argument;
    uint64_t sectors;

    assert(sd_protocol_divider(48000000u, 400000u, &divider));
    assert(divider == 120u && sd_protocol_actual_hz(48000000u, divider) == 400000u);
    assert(sd_protocol_divider(48000000u, 12000000u, &divider));
    assert(divider == 4u && sd_protocol_actual_hz(48000000u, divider) == 12000000u);
    assert(!sd_protocol_divider(48000000u, 1u, &divider));

    assert(sd_protocol_lba_argument(1234u, 1, &argument) && argument == 1234u);
    assert(sd_protocol_lba_argument(1234u, 0, &argument) && argument == 1234u * 512u);
    assert(!sd_protocol_lba_argument((uint64_t)UINT32_MAX / 512u + 1u, 0, &argument));
    assert(!sd_protocol_lba_argument((uint64_t)UINT32_MAX + 1u, 1, &argument));
    assert(sd_protocol_range_valid(100u, 99u, 1u));
    assert(!sd_protocol_range_valid(100u, 99u, 2u));
    assert(!sd_protocol_range_valid(100u, 100u, 1u));
    assert(!sd_protocol_range_valid(100u, 0u, 0u));

    csd[0] = 0x40u;
    csd[8] = 0x1fu; /* C_SIZE = 8191 -> 4 GiB card. */
    csd[9] = 0xffu;
    assert(sd_protocol_csd_sectors(csd, &sectors));
    assert(sectors == 8u * 1024u * 1024u);

    memset(csd, 0, sizeof(csd));
    csd[5] = 9u;       /* READ_BL_LEN = 512 bytes. */
    csd[6] = 0x03u;    /* C_SIZE high bits. */
    csd[7] = 0xffu;
    csd[8] = 0xc0u;
    csd[9] = 0x03u;    /* C_SIZE_MULT = 7. */
    csd[10] = 0x80u;
    assert(sd_protocol_csd_sectors(csd, &sectors));
    assert(sectors == (4096u * 512u));

    puts("SD protocol helpers passed");
    return 0;
}
