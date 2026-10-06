#ifndef TANG20K_SD_SPI_H
#define TANG20K_SD_SPI_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint8_t cid[16];
    uint8_t csd[16];
    uint8_t ocr[4];
    uint64_t sectors;
    uint32_t init_hz;
    uint32_t transfer_hz;
    uint8_t card_type;
    uint8_t initialized;
} sd_spi_info_t;

typedef void (*sd_spi_poll_fn)(void);

int sd_spi_initialize(void);
int sd_spi_card_status(void);
const sd_spi_info_t *sd_spi_info(void);
void sd_spi_set_poll_hook(sd_spi_poll_fn hook);
void fatfs_set_ops_spisdcard(void);

#endif
