#include "sdcard_app.h"
#include "sd_spi.h"

#include <generated/csr.h>
#include <generated/soc.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SD_APP_PATH_MAX 128u
#define SD_APP_BLOCK_BYTES 512u
#define SD_APP_MAX_FILE_BYTES (16u * 1024u * 1024u)

static unsigned int filesystem_busy;

int sdcard_app_busy(void) { return filesystem_busy != 0; }

static FATFS fatfs;
static int fatfs_mounted;
static char test_dir[16];
static uint32_t file_sequence;

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t length)
{
    size_t i;
    for (i = 0; i < length; ++i) {
        unsigned int bit;
        crc ^= data[i];
        for (bit = 0; bit < 8u; ++bit)
            crc = (crc & 1u) ? ((crc >> 1) ^ 0xedb88320u) : (crc >> 1);
    }
    return crc;
}

static uint32_t crc32_finish(uint32_t state)
{
    return state ^ 0xffffffffu;
}

static uint64_t uptime_cycles(void)
{
#ifdef CSR_TIMER0_UPTIME_CYCLES_ADDR
    timer0_uptime_latch_write(1);
    return timer0_uptime_cycles_read();
#else
    timer0_update_value_write(1);
    return 0xffffffffu - timer0_value_read();
#endif
}

static uint64_t uptime_ms(void)
{
    return (uptime_cycles() * 1000u) / CONFIG_CLOCK_FREQUENCY;
}

static const char *fr_name(FRESULT result)
{
    switch (result) {
    case FR_OK: return "ok";
    case FR_DISK_ERR: return "disk error";
    case FR_INT_ERR: return "internal error";
    case FR_NOT_READY: return "not ready";
    case FR_NO_FILE: return "no file";
    case FR_NO_PATH: return "no path";
    case FR_INVALID_NAME: return "invalid name";
    case FR_DENIED: return "access denied";
    case FR_EXIST: return "already exists";
    case FR_INVALID_OBJECT: return "invalid object";
    case FR_WRITE_PROTECTED: return "write protected";
    case FR_INVALID_DRIVE: return "invalid drive";
    case FR_NOT_ENABLED: return "not enabled";
    case FR_NO_FILESYSTEM: return "no supported FAT filesystem";
    case FR_MKFS_ABORTED: return "formatting disabled";
    case FR_TIMEOUT: return "filesystem timeout";
    case FR_LOCKED: return "locked";
    case FR_NOT_ENOUGH_CORE: return "out of memory";
    case FR_TOO_MANY_OPEN_FILES: return "too many open files";
    case FR_INVALID_PARAMETER: return "invalid parameter";
    default: return "unknown FatFs result";
    }
}

static int mount_filesystem(void)
{
    FRESULT result;
    if (!sd_spi_initialize()) {
        puts("SD error: no card or initialization failed (bounded timeout)");
        fatfs_mounted = 0;
        return 0;
    }
    if (fatfs_mounted)
        return 1;

    fatfs_set_ops_spisdcard();
    result = f_mount(&fatfs, "0:", 1);
    if (result != FR_OK) {
        (void)f_mount(NULL, "0:", 0);
        fatfs_mounted = 0;
        if (result == FR_NO_FILESYSTEM)
            puts("SD error: no FAT16/FAT32/exFAT volume; formatting is disabled");
        else
            printf("SD mount failed: %s (%u)\n", fr_name(result), (unsigned int)result);
        return 0;
    }
    if (fatfs.fs_type != FS_FAT16 && fatfs.fs_type != FS_FAT32 && fatfs.fs_type != FS_EXFAT) {
        (void)f_mount(NULL, "0:", 0);
        fatfs_mounted = 0;
        printf("SD filesystem unsupported: FAT type %u; only FAT16, FAT32 and exFAT are enabled\n",
               (unsigned int)fatfs.fs_type);
        return 0;
    }
    printf("SD mounted: %s\n", fatfs.fs_type == FS_EXFAT ? "exFAT" : fatfs.fs_type == FS_FAT32 ? "FAT32" : "FAT16");
    fatfs_mounted = 1;
    return 1;
}

static int require_free_space(uint32_t bytes)
{
    DWORD clusters;
    FATFS *volume;
    FRESULT result = f_getfree("0:", &clusters, &volume);
    if (result != FR_OK) {
        printf("SD free-space query failed: %s (%u)\n", fr_name(result), (unsigned int)result);
        return 0;
    }
    uint64_t free_bytes = (uint64_t)clusters * volume->csize * 512u;
    uint64_t required = (uint64_t)bytes + (uint64_t)volume->csize * 512u * 2u;
    printf("SD free_space bytes=%llu required=%llu\n",
           (unsigned long long)free_bytes, (unsigned long long)required);
    if (free_bytes < required) {
        puts("SD test refused: insufficient free space for a new file and directory metadata");
        return 0;
    }
    return 1;
}

static int format_path(const char *input, char output[SD_APP_PATH_MAX])
{
    size_t length;
    if (input == NULL || input[0] == '\0')
        return 0;
    length = strlen(input);
    if (length >= SD_APP_PATH_MAX - 3u)
        return 0;
    if (strncmp(input, "0:", 2) == 0) {
        memcpy(output, input, length + 1u);
    } else if (input[0] == '/') {
        output[0] = '0';
        output[1] = ':';
        memcpy(output + 2, input, length + 1u);
    } else {
        return 0;
    }
    for (length = 0; output[length] != '\0'; ++length) {
        if (output[length] == '.' && output[length + 1u] == '.')
            return 0;
    }
    return 1;
}

static void append_fixed_decimal(char *output, unsigned int value, unsigned int digits)
{
    while (digits != 0) {
        output[digits - 1u] = (char)('0' + (value % 10u));
        value /= 10u;
        --digits;
    }
}

static int ensure_test_directory(void)
{
    unsigned int attempt;
    FRESULT result;
    if (test_dir[0] != '\0')
        return 1;
    for (attempt = 0; attempt < 10000u; ++attempt) {
        unsigned int candidate = (unsigned int)((uptime_ms() + file_sequence + attempt) % 10000u);
        memcpy(test_dir, "0:/T20K", 7u);
        append_fixed_decimal(test_dir + 7, candidate, 4u);
        test_dir[11] = '\0';
        result = f_mkdir(test_dir);
        if (result == FR_OK)
            return 1;
        if (result != FR_EXIST) {
            printf("SD test directory creation failed: %s (%u)\n",
                   fr_name(result), (unsigned int)result);
            test_dir[0] = '\0';
            return 0;
        }
    }
    test_dir[0] = '\0';
    puts("SD error: could not find a new T20K test directory");
    return 0;
}

static int create_new_file(const char *prefix, char path[48], FIL *file)
{
    unsigned int attempts;
    FRESULT result;
    if (!ensure_test_directory())
        return 0;
    for (attempts = 0; attempts < 1000000u; ++attempts) {
        unsigned int sequence = file_sequence++ % 1000000u;
        size_t directory_length = strlen(test_dir);
        size_t prefix_length = strlen(prefix);
        memcpy(path, test_dir, directory_length);
        path[directory_length] = '/';
        memcpy(path + directory_length + 1u, prefix, prefix_length);
        append_fixed_decimal(path + directory_length + 1u + prefix_length,
                             sequence, 6u);
        memcpy(path + directory_length + 1u + prefix_length + 6u, ".BIN", 5u);
        result = f_open(file, path, FA_READ | FA_WRITE | FA_CREATE_NEW);
        if (result == FR_OK)
            return 1;
        if (result != FR_EXIST) {
            printf("SD new-file creation failed: %s (%u)\n",
                   fr_name(result), (unsigned int)result);
            return 0;
        }
    }
    puts("SD error: file sequence exhausted");
    return 0;
}

static int read_file_checksum(const char *path, uint32_t *length, uint32_t *checksum,
                              int show_preview)
{
    FIL file;
    FRESULT result;
    UINT bytes_read;
    uint8_t buffer[SD_APP_BLOCK_BYTES];
    uint8_t preview[32];
    size_t preview_length = 0;
    uint32_t total = 0;
    uint32_t crc = 0xffffffffu;

    result = f_open(&file, path, FA_READ);
    if (result != FR_OK) {
        printf("SD read open failed: %s (%u)\n", fr_name(result), (unsigned int)result);
        return 0;
    }
    if (f_size(&file) > UINT32_MAX) {
        puts("SD read: files above 4294967295 bytes exceed the checksum command's length limit");
        (void)f_close(&file);
        return 0;
    }
    do {
        result = f_read(&file, buffer, sizeof(buffer), &bytes_read);
        if (result != FR_OK)
            break;
        if (bytes_read > UINT32_MAX - total) {
            result = FR_INVALID_PARAMETER;
            break;
        }
        if (show_preview && preview_length < sizeof(preview)) {
            size_t remaining = sizeof(preview) - preview_length;
            size_t take = bytes_read < remaining ? bytes_read : remaining;
            memcpy(preview + preview_length, buffer, take);
            preview_length += take;
        }
        crc = crc32_update(crc, buffer, bytes_read);
        total += bytes_read;
    } while (bytes_read != 0);

    {
        FRESULT close_result = f_close(&file);
        if (result == FR_OK)
            result = close_result;
    }
    if (result != FR_OK) {
        printf("SD read failed: %s (%u)\n", fr_name(result), (unsigned int)result);
        if (result == FR_DISK_ERR || result == FR_NOT_READY)
            fatfs_mounted = 0;
        return 0;
    }
    if (length != NULL)
        *length = total;
    if (checksum != NULL)
        *checksum = crc32_finish(crc);
    if (show_preview) {
        size_t i;
        printf("preview:");
        for (i = 0; i < preview_length; ++i)
            printf(" %02x", preview[i]);
        puts("");
    }
    return 1;
}

static uint8_t test_pattern_byte(uint32_t position)
{
    return (uint8_t)(position * 37u + (position >> 8) + 0x5au);
}

static int run_roundtrip(uint32_t size)
{
    FIL file;
    FRESULT result;
    uint8_t buffer[SD_APP_BLOCK_BYTES];
    UINT bytes_written;
    uint32_t offset = 0;
    uint32_t expected_state = 0xffffffffu;
    uint32_t expected_crc;
    uint32_t read_length;
    uint32_t read_crc;
    uint64_t started;
    uint64_t elapsed;
    char path[48];

    if (!mount_filesystem())
        return 0;
    if (size == 0 || size > SD_APP_MAX_FILE_BYTES) {
        puts("SD roundtrip size must be 512 through 16777216 bytes");
        return 0;
    }
    if (!require_free_space(size) || !create_new_file("RT", path, &file))
        return 0;
    started = uptime_ms();

    while (offset < size) {
        uint32_t take = size - offset;
        uint32_t i;
        if (take > sizeof(buffer))
            take = sizeof(buffer);
        for (i = 0; i < take; ++i)
            buffer[i] = test_pattern_byte(offset + i);
        expected_state = crc32_update(expected_state, buffer, take);
        result = f_write(&file, buffer, take, &bytes_written);
        if (result != FR_OK || bytes_written != take) {
            printf("SD roundtrip write failed at %lu bytes: %s (%u), wrote %u\n",
                   (unsigned long)offset, fr_name(result), (unsigned int)result,
                   (unsigned int)bytes_written);
            (void)f_close(&file);
            fatfs_mounted = 0;
            return 0;
        }
        offset += take;
    }
    result = f_sync(&file);
    if (result != FR_OK) {
        printf("SD sync failed: %s (%u)\n", fr_name(result), (unsigned int)result);
        (void)f_close(&file);
        fatfs_mounted = 0;
        return 0;
    }
    result = f_close(&file);
    if (result != FR_OK) {
        printf("SD close failed: %s (%u)\n", fr_name(result), (unsigned int)result);
        fatfs_mounted = 0;
        return 0;
    }

    expected_crc = crc32_finish(expected_state);
    if (!read_file_checksum(path, &read_length, &read_crc, 0))
        return 0;
    elapsed = uptime_ms() - started;
    printf("SD roundtrip %s bytes=%lu crc32=%08lx elapsed_ms=%llu result=%s\n",
           path, (unsigned long)read_length, (unsigned long)read_crc,
           (unsigned long long)elapsed,
           read_length == size && read_crc == expected_crc ? "PASS" : "FAIL");
    return read_length == size && read_crc == expected_crc;
}

static void print_status(void)
{
    const sd_spi_info_t *info;
    unsigned int i;
    if (!sd_spi_card_status()) {
        (void)f_mount(NULL, "0:", 0);
        fatfs_mounted = 0;
        test_dir[0] = '\0';
        puts("SD status: unavailable (initialization timed out or card is missing)");
        return;
    }
    info = sd_spi_info();
    printf("SD status: ready type=0x%02x addressing=%s sectors=%llu bytes=%llu\n",
           info->card_type,
           (info->card_type & (1u << 3)) ? "block" : "byte",
           (unsigned long long)info->sectors,
           (unsigned long long)(info->sectors * 512u));
    printf("SPI clock: init=%lu Hz transfer=%lu Hz (limits 400000/12000000 Hz)\n",
           (unsigned long)info->init_hz, (unsigned long)info->transfer_hz);
    printf("CID:");
    for (i = 0; i < sizeof(info->cid); ++i)
        printf(" %02x", info->cid[i]);
    printf("\nCSD:");
    for (i = 0; i < sizeof(info->csd); ++i)
        printf(" %02x", info->csd[i]);
    printf("\nOCR:");
    for (i = 0; i < sizeof(info->ocr); ++i)
        printf(" %02x", info->ocr[i]);
    puts("");
}

static int list_directory(const char *requested_path)
{
    DIR directory;
    FILINFO entry;
    FRESULT result;
    char path[SD_APP_PATH_MAX];
    if (!mount_filesystem())
        return 0;
    if (requested_path == NULL || requested_path[0] == '\0')
        strcpy(path, "0:/");
    else if (!format_path(requested_path, path)) {
        puts("SD path must start with 0:/ or /");
        return 0;
    }
    result = f_opendir(&directory, path);
    if (result != FR_OK) {
        printf("SD directory open failed: %s (%u)\n", fr_name(result), (unsigned int)result);
        return 0;
    }
    printf("Directory %s\n", path);
    for (;;) {
        result = f_readdir(&directory, &entry);
        if (result != FR_OK || entry.fname[0] == '\0')
            break;
        printf("%c %10llu %s\n", (entry.fattrib & AM_DIR) ? 'd' : 'f',
               (unsigned long long)entry.fsize, entry.fname);
    }
    (void)f_closedir(&directory);
    if (result != FR_OK)
        printf("SD directory read failed: %s (%u)\n", fr_name(result), (unsigned int)result);
    else if (test_dir[0] != '\0')
        printf("New test directory for this boot: %s\n", test_dir);
    return result == FR_OK;
}

void sdcard_app_init(void)
{
    fatfs_set_ops_spisdcard();
    fatfs_mounted = 0;
    test_dir[0] = '\0';
    file_sequence = (uint32_t)uptime_ms();
    sd_spi_set_poll_hook(NULL);
}

static int sdcard_app_command_impl(const char *line)
{
    if (strncmp(line, "sd status", 9) == 0 && (line[9] == '\0' || line[9] == ' ')) {
        print_status();
        return 1;
    }
    if (strncmp(line, "sd ls", 5) == 0 && (line[5] == '\0' || line[5] == ' '))
        return list_directory(line[5] == '\0' ? NULL : line + 6), 1;
    if (strncmp(line, "sd read ", 8) == 0) {
        char path[SD_APP_PATH_MAX];
        uint32_t length;
        uint32_t checksum;
        if (!format_path(line + 8, path)) {
            puts("Usage: sd read /path/to/file");
            return 1;
        }
        if (mount_filesystem() && read_file_checksum(path, &length, &checksum, 1))
            printf("SD read %s bytes=%lu crc32=%08lx\n", path,
                   (unsigned long)length, (unsigned long)checksum);
        return 1;
    }
    if (strncmp(line, "sd roundtrip", 12) == 0
            && (line[12] == '\0' || line[12] == ' ')) {
        char *end = NULL;
        unsigned long size = SD_APP_BLOCK_BYTES;
        if (line[12] == ' ') {
            size = strtoul(line + 13, &end, 10);
            if (end == line + 13 || *end != '\0' || size > UINT32_MAX) {
                puts("Usage: sd roundtrip [512|4096|1048576]");
                return 1;
            }
        }
        if (size != 512u && size != 4096u && size != 1048576u) {
            puts("Usage: sd roundtrip [512|4096|1048576]");
            return 1;
        }
        (void)run_roundtrip((uint32_t)size);
        return 1;
    }
    if (strncmp(line, "sd ", 3) == 0) {
        puts("Usage: sd status | sd ls [/path] | sd read /path | sd roundtrip [512|4096|1048576]");
        return 1;
    }
    return 0;
}

const char *sdcard_test_directory(void)
{
    return test_dir;
}

static int sdcard_upload_begin_impl(sdcard_upload_t *upload, uint32_t expected_bytes,
                        uint32_t expected_crc)
{
    if (upload == NULL || expected_bytes == 0 || expected_bytes > SD_APP_MAX_FILE_BYTES
            || !mount_filesystem() || !require_free_space(expected_bytes))
        return 0;
    memset(upload, 0, sizeof(*upload));
    if (!create_new_file("UP", upload->path, &upload->file))
        return 0;
    upload->expected_bytes = expected_bytes;
    upload->expected_crc = expected_crc;
    upload->crc_state = 0xffffffffu;
    upload->open = 1;
    return 1;
}

static int sdcard_upload_write_impl(sdcard_upload_t *upload, const uint8_t *data, size_t length)
{
    FRESULT result;
    UINT written;
    if (upload == NULL || !upload->open || data == NULL || length == 0
            || length > UINT32_MAX - upload->received_bytes
            || length > upload->expected_bytes - upload->received_bytes)
        return 0;
    result = f_write(&upload->file, data, (UINT)length, &written);
    if (result != FR_OK || written != length) {
        printf("SD upload write failed: %s (%u), wrote %u/%u\n",
               fr_name(result), (unsigned int)result, (unsigned int)written,
               (unsigned int)length);
        (void)f_close(&upload->file);
        upload->open = 0;
        fatfs_mounted = 0;
        return 0;
    }
    upload->crc_state = crc32_update(upload->crc_state, data, length);
    upload->received_bytes += (uint32_t)length;
    return 1;
}

static int sdcard_upload_finish_impl(sdcard_upload_t *upload, uint32_t *actual_crc,
                         uint32_t *actual_bytes)
{
    FRESULT result;
    uint32_t file_length = 0;
    uint32_t file_crc = 0;
    int success;
    if (upload == NULL || !upload->open)
        return 0;

    result = f_sync(&upload->file);
    if (result == FR_OK)
        result = f_close(&upload->file);
    else
        (void)f_close(&upload->file);
    upload->open = 0;
    if (result != FR_OK) {
        printf("SD upload close/sync failed: %s (%u)\n", fr_name(result), (unsigned int)result);
        fatfs_mounted = 0;
        return 0;
    }

    success = upload->received_bytes == upload->expected_bytes
           && crc32_finish(upload->crc_state) == upload->expected_crc
           && read_file_checksum(upload->path, &file_length, &file_crc, 0)
           && file_length == upload->expected_bytes
           && file_crc == upload->expected_crc;
    if (actual_crc != NULL)
        *actual_crc = file_crc;
    if (actual_bytes != NULL)
        *actual_bytes = file_length;
    if (success)
        printf("SD upload PASS path=%s bytes=%lu crc32=%08lx reopened=yes\n",
               upload->path, (unsigned long)file_length, (unsigned long)file_crc);
    else
        printf("SD upload FAIL path=%s received=%lu expected=%lu incoming_crc=%08lx expected_crc=%08lx file_bytes=%lu file_crc=%08lx\n",
               upload->path, (unsigned long)upload->received_bytes,
               (unsigned long)upload->expected_bytes,
               (unsigned long)crc32_finish(upload->crc_state),
               (unsigned long)upload->expected_crc, (unsigned long)file_length,
               (unsigned long)file_crc);
    return success;
}

static void sdcard_upload_abort_impl(sdcard_upload_t *upload)
{
    if (upload != NULL && upload->open) {
        (void)f_sync(&upload->file);
        (void)f_close(&upload->file);
        upload->open = 0;
        printf("SD upload incomplete; retained new file %s (%lu/%lu bytes)\n",
               upload->path, (unsigned long)upload->received_bytes,
               (unsigned long)upload->expected_bytes);
    }
}

/* Guard complete filesystem operations, so SD poll callbacks never reenter FatFs. */
int sdcard_app_command(const char *line)
{
    int result;
    ++filesystem_busy;
    result = sdcard_app_command_impl(line);
    --filesystem_busy;
    return result;
}
int sdcard_upload_begin(sdcard_upload_t *file, uint32_t length, uint32_t crc)
{
    int result;
    ++filesystem_busy;
    result = sdcard_upload_begin_impl(file, length, crc);
    --filesystem_busy;
    return result;
}
int sdcard_upload_write(sdcard_upload_t *file, const uint8_t *data, size_t length)
{
    int result;
    ++filesystem_busy;
    result = sdcard_upload_write_impl(file, data, length);
    --filesystem_busy;
    return result;
}
int sdcard_upload_finish(sdcard_upload_t *file, uint32_t *crc, uint32_t *length)
{
    int result;
    ++filesystem_busy;
    result = sdcard_upload_finish_impl(file, crc, length);
    --filesystem_busy;
    return result;
}
void sdcard_upload_abort(sdcard_upload_t *file)
{
    ++filesystem_busy;
    sdcard_upload_abort_impl(file);
    --filesystem_busy;
}
