#ifndef TANG20K_SDCARD_APP_H
#define TANG20K_SDCARD_APP_H

#include "fatfs/ff.h"

#include <stddef.h>
#include <stdint.h>

typedef struct {
    FIL file;
    uint32_t expected_bytes;
    uint32_t expected_crc;
    uint32_t received_bytes;
    uint32_t crc_state;
    int open;
    char path[48];
} sdcard_upload_t;

void sdcard_app_init(void);
int sdcard_app_busy(void);
int sdcard_app_command(const char *line);
int sdcard_upload_begin(sdcard_upload_t *upload, uint32_t expected_bytes,
                        uint32_t expected_crc);
int sdcard_upload_write(sdcard_upload_t *upload, const uint8_t *data, size_t length);
int sdcard_upload_finish(sdcard_upload_t *upload, uint32_t *actual_crc,
                         uint32_t *actual_bytes);
void sdcard_upload_abort(sdcard_upload_t *upload);
const char *sdcard_test_directory(void);

#endif
