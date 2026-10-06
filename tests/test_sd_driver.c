#include <assert.h>
#include <stdint.h>
#include <stdio.h>
static uint8_t responses[64];
static uint8_t transmitted[64];
static unsigned received, selected, checks, stalled;
#include "../firmware/peripherals/sd_spi.c"
void spisdcard_mosi_write(uint32_t v) {assert(received<64);transmitted[received]=(uint8_t)v;}
void spisdcard_control_write(uint32_t v) {(void)v;}
void spisdcard_cs_write(uint32_t v) {selected=v;}
void spisdcard_clk_divider_write(uint32_t v) {(void)v;}
uint32_t spisdcard_clk_divider_read(void) {return 120;}
uint32_t spisdcard_status_read(void) {return stalled ? 0 : 1;}
uint32_t spisdcard_miso_read(void) {assert(received<64);return responses[received++];}
void timeout_start(struct timeout *t,unsigned int us) {(void)t;(void)us;checks=0;}
int timeout_expired(struct timeout *t) {(void)t;return ++checks>10;}
static void reset(void) {memset(responses,0xff,sizeof(responses));received=selected=checks=stalled=0;card.initialized=1;disk_status=0;}
int main(void) {
    const uint8_t cmd0[] = {0x40,0,0,0,0};
    const uint8_t cmd8[] = {0x48,0,0,1,0xaa};
    assert(sd_crc7(cmd0,5)==0x95 && sd_crc7(cmd8,5)==0x87);
    reset();card.sectors=50000000;
    uint8_t buffer[512];
    assert(sd_disk_read(0,buffer,0,UINT32_MAX/512u+1u)==RES_PARERR);
    assert(sd_disk_write(0,buffer,0,UINT32_MAX/512u+1u)==RES_PARERR);
    assert(received==0);
    reset();responses[9]=1;responses[19]=0;
    assert(sd_application_command(SD_ACMD41_HCS)==0);
    assert(transmitted[13]==(0x40|41) && transmitted[14]==0x40);
    assert(transmitted[15]==0 && transmitted[16]==0 && transmitted[17]==0);
    reset();responses[9]=responses[10]=0;
    assert(sd_spi_card_status()==1 && received==12 && selected==SD_CS_MANUAL && card.initialized);
    reset();assert(sd_spi_card_status()==0 && received==19 && !card.initialized && selected==SD_CS_MANUAL);
    assert(disk_status==(STA_NOINIT|STA_NODISK));
    reset();responses[9]=0;responses[10]=0x20;
    assert(sd_spi_card_status()==0 && !card.initialized && selected==SD_CS_MANUAL);
    reset();stalled=1;assert(sd_spi_card_status()==0 && checks==11 && !card.initialized && selected==SD_CS_MANUAL);
    reset();responses[9]=responses[10]=0;assert(sd_disk_status(0)==0 && received==12);
    assert(sd_disk_status(1)==STA_NOINIT && received==12);
    puts("SD live-status/removal/error/controller-timeout checks passed");return 0;
}
