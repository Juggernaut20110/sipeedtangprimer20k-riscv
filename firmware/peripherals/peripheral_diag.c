#include "peripheral_diag.h"
#include "profile.h"

#include <generated/csr.h>
#include <generated/mem.h>

#include <stdio.h>
#include <stdint.h>
#include <string.h>

void peripheral_diag_init(void)
{
    puts("Peripheral diagnostic firmware; full FAT/network services are not linked in this on-chip image.");
}

int peripheral_diag_command(const char *line)
{
    if (strcmp(line, "periph status") == 0) {
        printf("Peripheral diagnostic: profile=%s SD_SPI=%d ETHERNET_RMII=%d\n",
               CPU_PROFILE_NAME, PROJECT_SDCARD_SPI, PROJECT_ETHERNET_RMII);
#ifdef CSR_SPISDCARD_BASE
        puts("LiteX SPI SD controller: present (CSR generated)");
#else
        puts("LiteX SPI SD controller: disabled");
#endif
#ifdef CSR_ETHMAC_BASE
        printf("LiteEth MAC: present at %p; RX slots=%u TX slots=%u slot bytes=%u\n",
               (void *)(uintptr_t)ETHMAC_BASE, (unsigned int)ETHMAC_RX_SLOTS,
               (unsigned int)ETHMAC_TX_SLOTS, (unsigned int)ETHMAC_SLOT_SIZE);
#else
        puts("LiteEth MAC: disabled");
#endif
        return 1;
    }
    if (strncmp(line, "periph ", 7) == 0) {
        puts("Usage: periph status");
        return 1;
    }
    return 0;
}
