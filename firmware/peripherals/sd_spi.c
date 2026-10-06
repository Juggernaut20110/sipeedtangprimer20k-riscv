#include "sd_spi.h"
#include "sd_protocol.h"

#include <generated/csr.h>
#include <generated/soc.h>
#include <libbase/timeout.h>
#include "fatfs/diskio.h"
#include "fatfs/ff.h"

#include <limits.h>
#include <string.h>
#include <stdio.h>

#define SD_SPI_INIT_MAX_HZ      400000u
#define SD_SPI_TRANSFER_MAX_HZ  12000000u
#define SD_SPI_XFER_TIMEOUT_US  10000u
#define SD_COMMAND_TIMEOUT_US   100000u
#define SD_WRITE_TIMEOUT_US     500000u
#define SD_CS_MANUAL            (1u << 16)
#define SD_CS_SELECTED          (1u << 0)
#define SD_SPI_DONE             (1u << 0)
#define SD_SPI_ERROR            (1u << 2)
#define SD_SPI_START            (1u << 0)
#define SD_SPI_LENGTH_8         (8u << 8)
#define SD_DATA_START           0xfeu
#define SD_DATA_ACCEPTED        0x05u
#define SD_R1_IDLE              0x01u
#define SD_R1_ILLEGAL_COMMAND   0x04u
#define SD_CCS                  0x40u
#define SD_ACMD41_HCS           (1u << 30)

#define SD_CMD0    0u
#define SD_CMD1    1u
#define SD_CMD8    8u
#define SD_CMD9    9u
#define SD_CMD10   10u
#define SD_CMD12   12u
#define SD_CMD13   13u
#define SD_CMD16   16u
#define SD_CMD17   17u
#define SD_CMD24   24u
#define SD_CMD55   55u
#define SD_CMD59   59u
#define SD_CMD58   58u
#define SD_ACMD41  41u

enum {
    SD_TYPE_UNKNOWN = 0,
    SD_TYPE_SD_V1   = 1u << 0,
    SD_TYPE_SD_V2   = 1u << 1,
    SD_TYPE_MMC     = 1u << 2,
    SD_TYPE_BLOCK   = 1u << 3,
};

static sd_spi_info_t card;
static DSTATUS disk_status = STA_NOINIT;
static sd_spi_poll_fn poll_hook;
static uint8_t transfers_before_poll;
static uint8_t card_crc_enabled;
static const char *init_stage;
static uint8_t last_command;
static int last_response;
static uint8_t response_bytes[10];
static unsigned response_count;

static uint8_t sd_crc7(const uint8_t *data, size_t length)
{
    uint8_t crc = 0;
    size_t i;
    for (i = 0; i < length; ++i) {
        uint8_t byte = data[i];
        unsigned int bit;
        for (bit = 0; bit < 8u; ++bit) {
            crc <<= 1;
            if (((byte ^ crc) & 0x80u) != 0)
                crc ^= 0x09u;
            byte <<= 1;
        }
    }
    return (uint8_t)((crc & 0x7fu) << 1 | 1u);
}

static uint16_t sd_crc16(const uint8_t *data, size_t length)
{
    uint16_t crc = 0;
    size_t i;
    for (i = 0; i < length; ++i) {
        unsigned int bit;
        crc ^= (uint16_t)data[i] << 8;
        for (bit = 0; bit < 8u; ++bit)
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
    }
    return crc;
}

static void sd_mark_failed(void)
{
    card.initialized = 0;
    disk_status |= STA_NOINIT;
    card_crc_enabled = 0;
    spisdcard_cs_write(SD_CS_MANUAL);
}

void sd_spi_set_poll_hook(sd_spi_poll_fn hook)
{
    poll_hook = hook;
}

const sd_spi_info_t *sd_spi_info(void)
{
    return &card;
}

static void sd_poll_if_due(void)
{
    if (++transfers_before_poll >= 32u) {
        transfers_before_poll = 0;
        if (poll_hook != NULL)
            poll_hook();
    }
}

static int sd_spi_transfer(uint8_t tx)
{
    struct timeout timeout;

    spisdcard_mosi_write(tx);
    spisdcard_control_write(SD_SPI_LENGTH_8 | SD_SPI_START);
    /* Fast transfers may finish before the first CSR read. Avoid expensive
       64-bit deadline setup for every completed byte on the RV32I core. */
    if ((spisdcard_status_read() & SD_SPI_DONE) == 0) {
        timeout_start(&timeout, SD_SPI_XFER_TIMEOUT_US);
        while ((spisdcard_status_read() & SD_SPI_DONE) == 0) {
            if (timeout_expired(&timeout))
                return -1;
        }
    }
    if (spisdcard_status_read() & SD_SPI_ERROR)
        return -1;
    sd_poll_if_due();
    return (int)(spisdcard_miso_read() & 0xffu);
}

static int sd_set_clock(uint32_t target_hz, uint32_t *actual_hz)
{
    uint16_t divider;
    if (!sd_protocol_divider(CONFIG_CLOCK_FREQUENCY, target_hz, &divider))
        return 0;
    spisdcard_clk_divider_write(divider);
    if (actual_hz != NULL)
        *actual_hz = sd_protocol_actual_hz(CONFIG_CLOCK_FREQUENCY, divider);
    return 1;
}

static int sd_deselect(void)
{
    spisdcard_cs_write(SD_CS_MANUAL);
    return sd_spi_transfer(0xffu) >= 0;
}

static int sd_select(void)
{
    struct timeout timeout;
    int value;

    spisdcard_cs_write(SD_CS_MANUAL | SD_CS_SELECTED);
    timeout_start(&timeout, SD_WRITE_TIMEOUT_US);
    do {
        value = sd_spi_transfer(0xffu);
        if (value < 0)
            return 0;
        if (value == 0xff)
            return 1;
    } while (!timeout_expired(&timeout));
    return 0;
}

static int sd_wait_ready(unsigned int timeout_us)
{
    struct timeout timeout;
    int value;

    timeout_start(&timeout, timeout_us);
    do {
        value = sd_spi_transfer(0xffu);
        if (value < 0)
            return 0;
        if (value == 0xff)
            return 1;
    } while (!timeout_expired(&timeout));
    return 0;
}

static int sd_send_selected(uint8_t command, uint32_t argument)
{
    uint8_t packet[6];
    unsigned int i;
    int value = 0xff;

    last_command = command;
    last_response = -1;
    response_count = 0;

    packet[0] = (uint8_t)(0x40u | command);
    packet[1] = (uint8_t)(argument >> 24);
    packet[2] = (uint8_t)(argument >> 16);
    packet[3] = (uint8_t)(argument >> 8);
    packet[4] = (uint8_t)argument;
    packet[5] = sd_crc7(packet, 5);
    for (i = 0; i < sizeof(packet); ++i) {
        if (sd_spi_transfer(packet[i]) < 0)
            return -1;
    }

    for (i = 0; i < 10u; ++i) {
        value = sd_spi_transfer(0xffu);
        if (value < 0)
            return -1;
        response_bytes[response_count++] = (uint8_t)value;
        if ((value & 0x80) == 0)
            return last_response = value;
    }
    return -1;
}

static int sd_command(uint8_t command, uint32_t argument)
{
    if (!sd_deselect())
        return -1;
    /* Reset must reach a card even when MISO is busy or its state is unknown. */
    if (command == SD_CMD0) {
        spisdcard_cs_write(SD_CS_MANUAL | SD_CS_SELECTED);
        if (sd_spi_transfer(0xffu) < 0)
            return -1;
    } else if (!sd_select()) {
        return -1;
    }
    if (command != SD_CMD0 && command != SD_CMD12
            && !sd_wait_ready(SD_WRITE_TIMEOUT_US))
        return -1;
    return sd_send_selected(command, argument);
}

static int sd_application_command(uint32_t argument)
{
    int response = sd_command(SD_CMD55, 0);
    if (response < 0 || (response & SD_R1_ILLEGAL_COMMAND))
        return response;
    /* Supply the response-to-command clocks and a fresh CS transaction. */
    return sd_command(SD_ACMD41, argument);
}

static int sd_read_data(uint8_t *buffer, size_t length)
{
    struct timeout timeout;
    size_t i;
    uint16_t expected_crc;
    uint16_t received_crc;
    int value;

    timeout_start(&timeout, SD_COMMAND_TIMEOUT_US);
    for (;;) {
        value = sd_spi_transfer(0xffu);
        if (value < 0)
            return 0;
        if (value == SD_DATA_START)
            break;
        if (value != 0xff)
            return 0;
        if (timeout_expired(&timeout))
            return 0;
    }

    for (i = 0; i < length; ++i) {
        value = sd_spi_transfer(0xffu);
        if (value < 0)
            return 0;
        buffer[i] = (uint8_t)value;
    }
    value = sd_spi_transfer(0xffu);
    if (value < 0)
        return 0;
    expected_crc = (uint16_t)value << 8;
    value = sd_spi_transfer(0xffu);
    if (value < 0)
        return 0;
    expected_crc |= (uint16_t)value;
    received_crc = sd_crc16(buffer, length);
    if (card_crc_enabled && received_crc != expected_crc)
        return 0;
    return 1;
}

static int sd_read_register(uint8_t command, uint8_t value[16])
{
    int response = sd_command(command, 0);
    int result = response == 0 && sd_read_data(value, 16);
    if (!sd_deselect())
        result = 0;
    return result;
}

static int sd_spi_initialize_inner(void)
{
    struct timeout timeout;
    uint8_t r7[4];
    int response;
    int version2 = 0;
    int mmc = 0;
    unsigned int i;

    memset(&card, 0, sizeof(card));
    card_crc_enabled = 0;
    disk_status = STA_NOINIT;
    init_stage = "initial clocks";
    if (!sd_set_clock(SD_SPI_INIT_MAX_HZ, &card.init_hz))
        return 0;

    spisdcard_cs_write(SD_CS_MANUAL);
    for (i = 0; i < 10u; ++i) {
        if (sd_spi_transfer(0xffu) < 0)
            return 0;
    }

    init_stage = "CMD0";
    timeout_start(&timeout, 1000000u);
    do {
        response = sd_command(SD_CMD0, 0);
        if (response == SD_R1_IDLE)
            break;
        if (timeout_expired(&timeout))
            return 0;
    } while (1);

    init_stage = "CMD8/R7";
    response = sd_command(SD_CMD8, 0x000001aau);
    if (response == SD_R1_IDLE) {
        for (i = 0; i < sizeof(r7); ++i) {
            int value = sd_spi_transfer(0xffu);
            if (value < 0)
                return 0;
            r7[i] = (uint8_t)value;
        }
        if ((r7[2] & 0x0fu) != 0x01u || r7[3] != 0xaau)
            return 0;
        version2 = 1;
        card.card_type |= SD_TYPE_SD_V2;
    } else if (response == (SD_R1_IDLE | SD_R1_ILLEGAL_COMMAND)) {
        card.card_type |= SD_TYPE_SD_V1;
    } else {
        return 0;
    }
    if (!sd_deselect())
        return 0;

    init_stage = "ACMD41";
    timeout_start(&timeout, 1000000u);
    for (;;) {
        response = sd_application_command(version2 ? SD_ACMD41_HCS : 0u);
        if (response == 0)
            break;
        if (response == (SD_R1_IDLE | SD_R1_ILLEGAL_COMMAND)) {
            mmc = 1;
            break;
        }
        if (response != SD_R1_IDLE || timeout_expired(&timeout))
            return 0;
    }

    if (mmc) {
        init_stage = "CMD1";
        card.card_type = SD_TYPE_MMC;
        timeout_start(&timeout, 1000000u);
        do {
            response = sd_command(SD_CMD1, 0);
            if (response == 0)
                break;
            if (response != SD_R1_IDLE || timeout_expired(&timeout))
                return 0;
        } while (1);
    }

    init_stage = "CMD58/OCR";
    response = sd_command(SD_CMD58, 0);
    if (response != 0)
        return 0;
    for (i = 0; i < sizeof(card.ocr); ++i) {
        int value = sd_spi_transfer(0xffu);
        if (value < 0)
            return 0;
        card.ocr[i] = (uint8_t)value;
    }
    if (!sd_deselect() || (card.ocr[0] & 0x80u) == 0)
        return 0;
    if ((card.ocr[0] & SD_CCS) != 0 && !mmc)
        card.card_type |= SD_TYPE_BLOCK;

    if ((card.card_type & SD_TYPE_BLOCK) == 0) {
        init_stage = "CMD16";
        response = sd_command(SD_CMD16, 512u);
        if (response != 0 || !sd_deselect())
            return 0;
    }

    init_stage = "CMD59";
    response = sd_command(SD_CMD59, 1u);
    if (response == 0) {
        card_crc_enabled = 1;
        if (!sd_deselect())
            return 0;
    } else if ((response & SD_R1_ILLEGAL_COMMAND) != 0) {
        if (!sd_deselect())
            return 0;
    } else {
        return 0;
    }

    if (!sd_set_clock(SD_SPI_TRANSFER_MAX_HZ, &card.transfer_hz))
        return 0;
    init_stage = "CMD10/CID";
    if (!sd_read_register(SD_CMD10, card.cid))
        return 0;
    init_stage = "CMD9/CSD";
    if (!sd_read_register(SD_CMD9, card.csd))
        return 0;
    if (!sd_protocol_csd_sectors(card.csd, &card.sectors)
            || card.sectors > UINT32_MAX)
        return 0;

    card.initialized = 1;
    disk_status = 0;
    return 1;
}

int sd_spi_initialize(void)
{
    if (card.initialized)
        return 1;
    if (!sd_spi_initialize_inner()) {
        printf("SD init failed: stage=%s command=%u response=%d SPI_status=%lu divider=%lu\n",
               init_stage, (unsigned)last_command, last_response,
               (unsigned long)spisdcard_status_read(),
               (unsigned long)spisdcard_clk_divider_read());
        printf("SD response bytes:");
        for (unsigned i = 0; i < response_count; ++i)
            printf(" %02x", response_bytes[i]);
        printf("\n");
        sd_mark_failed();
        return 0;
    }
    return 1;
}

int sd_spi_card_status(void)
{
    int response;
    if (!sd_spi_initialize())
        return 0;
    response = sd_command(SD_CMD13, 0);
    if (response == 0)
        response = sd_spi_transfer(0xffu);
    if (response != 0 || !sd_deselect()) {
        sd_mark_failed();
        disk_status |= STA_NODISK;
        return 0;
    }
    return 1;
}

static DSTATUS sd_disk_initialize(BYTE drive)
{
    if (drive != 0)
        return STA_NOINIT;
    if (!sd_spi_initialize())
        return STA_NOINIT;
    return disk_status;
}

static DSTATUS sd_disk_status(BYTE drive)
{
    if (drive != 0)
        return STA_NOINIT;
    if (card.initialized)
        (void)sd_spi_card_status();
    return disk_status;
}

static DRESULT sd_disk_read(BYTE drive, BYTE *buffer, LBA_t first, UINT count)
{
    UINT i;
    if (drive != 0 || buffer == NULL || count == 0 || count > UINT32_MAX / 512u)
        return RES_PARERR;
    if (!sd_spi_initialize())
        return RES_NOTRDY;
    if (!sd_protocol_range_valid(card.sectors, first, count))
        return RES_PARERR;

    for (i = 0; i < count; ++i) {
        uint32_t argument;
        int response;
        if (!sd_protocol_lba_argument((uint64_t)first + i,
                (card.card_type & SD_TYPE_BLOCK) != 0, &argument))
            return RES_PARERR;
        response = sd_command(SD_CMD17, argument);
        if (response != 0 || !sd_read_data(buffer + i * 512u, 512u) || !sd_deselect()) {
            sd_mark_failed();
            return RES_ERROR;
        }
    }
    return RES_OK;
}

static int sd_write_sector(uint32_t argument, const uint8_t *buffer)
{
    unsigned int i;
    int response;
    uint16_t crc;

    response = sd_command(SD_CMD24, argument);
    if (response != 0)
        return 0;
    if (sd_spi_transfer(SD_DATA_START) < 0)
        return 0;
    for (i = 0; i < 512u; ++i) {
        if (sd_spi_transfer(buffer[i]) < 0)
            return 0;
    }
    crc = sd_crc16(buffer, 512u);
    if (sd_spi_transfer((uint8_t)(crc >> 8)) < 0
            || sd_spi_transfer((uint8_t)crc) < 0)
        return 0;
    response = sd_spi_transfer(0xffu);
    if (response < 0 || (response & 0x1fu) != SD_DATA_ACCEPTED)
        return 0;
    if (!sd_wait_ready(SD_WRITE_TIMEOUT_US) || !sd_deselect())
        return 0;

    /* CMD13 exposes card-side write/program errors after the busy interval. */
    response = sd_command(SD_CMD13, 0);
    if (response != 0)
        return 0;
    response = sd_spi_transfer(0xffu);
    if (response != 0 || !sd_deselect())
        return 0;
    return 1;
}

static DRESULT sd_disk_write(BYTE drive, const BYTE *buffer, LBA_t first, UINT count)
{
    UINT i;
    if (drive != 0 || buffer == NULL || count == 0 || count > UINT32_MAX / 512u)
        return RES_PARERR;
    if (!sd_spi_initialize())
        return RES_NOTRDY;
    if (!sd_protocol_range_valid(card.sectors, first, count))
        return RES_PARERR;

    for (i = 0; i < count; ++i) {
        uint32_t argument;
        if (!sd_protocol_lba_argument((uint64_t)first + i,
                (card.card_type & SD_TYPE_BLOCK) != 0, &argument))
            return RES_PARERR;
        if (!sd_write_sector(argument, buffer + i * 512u)) {
            sd_mark_failed();
            return RES_ERROR;
        }
    }
    return RES_OK;
}

static DRESULT sd_disk_ioctl(BYTE drive, BYTE command, void *buffer)
{
    if (drive != 0)
        return RES_PARERR;
    if (!sd_spi_initialize())
        return RES_NOTRDY;

    switch (command) {
    case CTRL_SYNC:
        if (!sd_select() || !sd_wait_ready(SD_WRITE_TIMEOUT_US) || !sd_deselect()) {
            sd_mark_failed();
            return RES_ERROR;
        }
        return RES_OK;
    case GET_SECTOR_COUNT:
        if (buffer == NULL)
            return RES_PARERR;
        *(LBA_t *)buffer = (LBA_t)card.sectors;
        return RES_OK;
    case GET_SECTOR_SIZE:
        if (buffer == NULL)
            return RES_PARERR;
        *(WORD *)buffer = 512u;
        return RES_OK;
    case GET_BLOCK_SIZE:
        if (buffer == NULL)
            return RES_PARERR;
        *(DWORD *)buffer = 1u;
        return RES_OK;
    default:
        return RES_PARERR;
    }
}

static DISKOPS sd_disk_ops = {
    .disk_initialize = sd_disk_initialize,
    .disk_status = sd_disk_status,
    .disk_read = sd_disk_read,
    .disk_write = sd_disk_write,
    .disk_ioctl = sd_disk_ioctl,
};

void fatfs_set_ops_spisdcard(void)
{
    FfDiskOps = &sd_disk_ops;
}
