#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static uint8_t mock_rx[4096], mock_tx[4096];
#include "../firmware/peripherals/ethernet_lwip.c"
static unsigned pending, frame_length, frame_slot, freed, aborted, acknowledged;
static unsigned writes, begin_calls, finish_calls, abort_calls;
static unsigned flood, released;
static int fs_busy, fail_alloc;
static err_t write_result, close_result;
static uint8_t written[20000];
static uint8_t mdio_bits[512];
static uint16_t mdio_read_values[4];
static unsigned mdio_read_bit;
uint32_t ethphy_mdio_r_read(void) {
    assert(mdio_read_bit<64);
    unsigned bit=mdio_read_bit++;
    return (mdio_read_values[bit/16] & (1u<<(15-bit%16))) ? MDIO_DI : 0;
}
static uint32_t mdio_word(unsigned offset) {
    uint32_t word=0;for(unsigned i=0;i<32;++i)word=(word<<1)|mdio_bits[offset+i];
    return word;
}
static unsigned mdio_clocked;
void ethphy_mdio_w_write(uint32_t value) {
    if ((value & (MDIO_OE|MDIO_CLK)) == (MDIO_OE|MDIO_CLK)) {
        assert(mdio_clocked<sizeof(mdio_bits));mdio_bits[mdio_clocked++]=(value&MDIO_DO)!=0;
    }
}
uint32_t ethmac_sram_writer_ev_pending_read(void) { return pending; }
uint32_t ethmac_sram_writer_slot_read(void) { return frame_slot; }
uint32_t ethmac_sram_writer_length_read(void) { return frame_length; }
void ethmac_sram_writer_ev_pending_write(uint32_t value) { (void)value; ++released; if(!flood)pending=0; }
void *mem_malloc(mem_size_t size) { return fail_alloc ? NULL : malloc(size); }
void mem_free(void *p) { free(p); }
u16_t pbuf_copy_partial(const struct pbuf *p, void *d, u16_t len, u16_t offset) {
    memcpy(d,(uint8_t *)p->payload+offset,len); return len;
}
u8_t pbuf_free(struct pbuf *p) { (void)p; ++freed; return 1; }
err_t tcp_close(struct tcp_pcb *p) { (void)p; return close_result; }
void tcp_abort(struct tcp_pcb *p) { ++aborted; if(p->errf) p->errf(p->callback_arg,ERR_ABRT); }
err_t tcp_write(struct tcp_pcb *p,const void *d,u16_t len,u8_t flags) {
    (void)p;(void)flags; if(write_result==ERR_OK){memcpy(written,d,len);++writes;} return write_result;
}
void tcp_recved(struct tcp_pcb *p,u16_t len) { (void)p; acknowledged+=len; }
err_t tcp_output(struct tcp_pcb *p) { (void)p;return ERR_OK; }
void tcp_arg(struct tcp_pcb *p,void *a) { p->callback_arg=a; }
void tcp_err(struct tcp_pcb *p,tcp_err_fn f) { p->errf=f; }
void tcp_recv(struct tcp_pcb *p,tcp_recv_fn f) { p->recv=f; }
int sdcard_app_busy(void) { return fs_busy; }
int sdcard_upload_begin(sdcard_upload_t *f,uint32_t length,uint32_t crc) {
    ++begin_calls; f->expected_bytes=length;f->expected_crc=crc;f->received_bytes=0;f->open=1;return 1;
}
int sdcard_upload_write(sdcard_upload_t *f,const uint8_t *d,size_t length) {
    (void)d; if(length>f->expected_bytes-f->received_bytes)return 0;
    f->received_bytes+=length;return 1;
}
int sdcard_upload_finish(sdcard_upload_t *f,uint32_t *crc,uint32_t *length) {
    ++finish_calls; *crc=f->expected_crc;*length=f->received_bytes;f->open=0;return 1;
}
void sdcard_upload_abort(sdcard_upload_t *f) { ++abort_calls;f->open=0; }
static struct pbuf packet(uint8_t *bytes,u16_t length) {
    struct pbuf p;memset(&p,0,sizeof(p));p.payload=bytes;p.len=p.tot_len=length;return p;
}
int main(void) {
    mdio_write(3,4,0x01e1);
    assert(mdio_clocked==64);
    uint32_t preamble=0,command=0;
    for(unsigned i=0;i<32;++i){preamble=(preamble<<1)|mdio_bits[i];command=(command<<1)|mdio_bits[i+32];}
    assert(preamble==UINT32_MAX);
    assert(command==((1u<<30)|(1u<<28)|(3u<<23)|(4u<<18)|(2u<<16)|0x01e1u));
    /* Preserve factory delay offsets and page while selecting PHY clock output. */
    mdio_clocked=mdio_read_bit=0;
    mdio_read_values[0]=2;mdio_read_values[1]=0x1ab0;mdio_read_values[2]=0x0ab8;
    configure_realtek_rmii(3);
    assert(phy_rmii_mode==0x0ab8 && mdio_read_bit==48 && mdio_clocked==330);
    assert((mdio_word(78)&0xffff)==7);
    assert((mdio_word(188)&0xffff)==0x0ab8);
    assert((mdio_word(298)&0xffff)==2);
    /* Already-correct mode skips register 16 writes; unavailable mode is untouched. */
    mdio_clocked=mdio_read_bit=0;mdio_read_values[1]=0x0ab8;
    configure_realtek_rmii(3);
    assert(mdio_clocked==266 && mdio_read_bit==48 && (mdio_word(234)&0xffff)==2);
    mdio_clocked=mdio_read_bit=0;mdio_read_values[1]=0xffff;
    configure_realtek_rmii(3);
    assert(mdio_clocked==220 && mdio_read_bit==32 && (mdio_word(188)&0xffff)==2);
    struct tcp_pcb pcb;memset(&pcb,0,sizeof(pcb));pcb.snd_buf=12000;
    uint8_t bytes[4000];for(unsigned i=0;i<sizeof(bytes);++i)bytes[i]=(uint8_t)i;
    /* Oversize and invalid slots must release hardware frames without copying. */
    pending=1;frame_length=2048;rx_capture();assert(rx_bad_frames==1 && rx_head==0 && !pending);
    pending=1;frame_length=1518;frame_slot=2;rx_capture();assert(rx_bad_frames==2 && rx_head==0);
    frame_slot=0;memcpy(mock_rx,bytes,1518);pending=1;rx_capture();
    assert(rx_head==1 && rx_queue[0].length==1518 && !memcmp(rx_queue[0].bytes,bytes,1518));
    unsigned old_released=released;pending=flood=1;rx_capture();
    assert(released-old_released==ETH_RX_QUEUE_COUNT && pending==1);
    assert(rx_head==ETH_RX_QUEUE_COUNT && rx_queue_drops==1);
    pending=flood=0;rx_head=rx_tail=0;
    uint8_t mac[6];assert(!parse_mac("",mac) && !parse_mac("0",mac));
    assert(parse_mac("02:20:20:00:00:01",mac));
    assert(!parse_mac("01:20:20:00:00:01",mac));
    /* A chained TCP payload can exceed one Ethernet frame. Retry retains its pbuf. */
    struct pbuf p=packet(bytes,4000);
    fail_alloc=1;assert(tcp_echo_received(NULL,&pcb,&p,ERR_OK)==ERR_MEM && freed==0);
    fail_alloc=0;write_result=ERR_MEM;assert(tcp_echo_received(NULL,&pcb,&p,ERR_OK)==ERR_MEM && freed==0);
    write_result=ERR_OK;assert(tcp_echo_received(NULL,&pcb,&p,ERR_OK)==ERR_OK);
    assert(freed==1 && acknowledged==4000 && !memcmp(written,bytes,4000));
    close_result=ERR_MEM;assert(tcp_echo_received(NULL,&pcb,NULL,ERR_OK)==ERR_ABRT && aborted==1);
    close_result=ERR_OK;
    /* No filesystem calls inside receive callbacks; bounded queue preserves data. */
    assert(upload_tcp_accept(NULL,&pcb,ERR_OK)==ERR_OK);
    uint8_t header_payload[520];write_be32(header_payload,512);write_be32(header_payload+4,0x12345678);
    memcpy(header_payload+8,bytes,512);p=packet(header_payload,520);
    unsigned old_ack=acknowledged;
    assert(upload_received(&upload_connection,&pcb,&p,ERR_OK)==ERR_OK);
    assert(begin_calls==0 && acknowledged==old_ack);
    fs_busy=1;upload_service();assert(begin_calls==0);fs_busy=0;
    write_result=ERR_MEM;upload_service();assert(begin_calls==1 && finish_calls==1 && upload_connection.reply_ready);
    assert(acknowledged==old_ack+520 && upload_connection.pcb==&pcb);
    write_result=ERR_OK;close_result=ERR_MEM;upload_service();
    assert(upload_connection.reply_sent && upload_connection.pcb==&pcb && pcb.errf==upload_tcp_error);
    unsigned old_writes=writes;close_result=ERR_OK;upload_service();
    assert(upload_connection.pcb==NULL && writes==old_writes && written[0]=='O' && written[1]=='K');
    upload_service();assert(upload_tcp_accept(NULL,&pcb,ERR_OK)==ERR_OK);
    upload_connection.head=UPLOAD_QUEUE_BYTES;upload_connection.tail=0;p=packet(bytes,1);
    unsigned old_free=freed;assert(upload_received(&upload_connection,&pcb,&p,ERR_OK)==ERR_MEM);
    assert(freed==old_free && upload_connection.head==UPLOAD_QUEUE_BYTES);
    upload_connection.active=1;upload_tcp_error(&upload_connection,ERR_RST);
    assert(abort_calls==0);upload_service();assert(abort_calls==1 && !upload_connection.abort_pending);
    /* Ring wrap preserves payload order. */
    assert(upload_tcp_accept(NULL,&pcb,ERR_OK)==ERR_OK);
    upload_connection.head=upload_connection.tail=UPLOAD_QUEUE_BYTES-2;p=packet(bytes,4);
    assert(upload_received(&upload_connection,&pcb,&p,ERR_OK)==ERR_OK);
    assert(upload_connection.bytes[UPLOAD_QUEUE_BYTES-2]==bytes[0] && upload_connection.bytes[1]==bytes[3]);
    puts("Ethernet driver callbacks passed");return 0;
}
