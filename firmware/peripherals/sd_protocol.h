#ifndef TANG20K_SD_PROTOCOL_H
#define TANG20K_SD_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

int sd_protocol_divider(uint32_t sys_hz, uint32_t target_hz, uint16_t *divider);
uint32_t sd_protocol_actual_hz(uint32_t sys_hz, uint16_t divider);
int sd_protocol_lba_argument(uint64_t lba, int block_addressed, uint32_t *argument);
int sd_protocol_range_valid(uint64_t total_sectors, uint64_t first, uint32_t count);
int sd_protocol_csd_sectors(const uint8_t csd[16], uint64_t *sectors);

#endif
