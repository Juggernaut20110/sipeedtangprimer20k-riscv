#include "ethernet_lwip.h"
#include "lwip_sys.h"
#include "profile.h"
#if PROJECT_SDCARD_SPI
#include "sdcard_app.h"
#include "sd_spi.h"
#endif

#include <generated/csr.h>
#include <generated/mem.h>
#include <generated/soc.h>
#include <libbase/timeout.h>

#include <lwip/dhcp.h>
#include <lwip/etharp.h>
#include <lwip/init.h>
#include <lwip/mem.h>
#include <lwip/ip4_addr.h>
#include <lwip/netif.h>
#include <lwip/pbuf.h>
#include <lwip/sys.h>
#include <lwip/tcp.h>
#include <lwip/priv/tcp_priv.h>
#include <lwip/timeouts.h>
#include <lwip/udp.h>
#include <netif/ethernet.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if ETHMAC_SLOT_SIZE < 1518
#error "LiteEth packet slots must hold standard Ethernet frames"
#endif

#define ETH_EVENT_AVAILABLE 1u
#define ETH_RX_QUEUE_COUNT  4u
#define ETH_MAX_FRAME       1518u
#define ETH_UDP_ECHO_PORT   5001u
#define ETH_TCP_ECHO_PORT   5002u
#define ETH_TCP_UPLOAD_PORT 5003u
#define UPLOAD_QUEUE_BYTES 16384u
#define MDIO_CLK            0x01u
#define MDIO_OE             0x02u
#define MDIO_DO             0x04u
#define MDIO_DI             0x01u

typedef struct {
    uint16_t length;
    uint8_t bytes[ETH_MAX_FRAME];
} rx_frame_t;

#if PROJECT_SDCARD_SPI
typedef struct {
    struct tcp_pcb *pcb;
    sdcard_upload_t file;
    uint8_t header[8];
    uint8_t header_bytes;
    uint8_t active;
    uint8_t peer_closed;
    uint8_t abort_pending;
    uint8_t reply_ready;
    uint8_t reply_sent;
    uint8_t response[10];
    uint32_t head;
    uint32_t tail;
    uint8_t bytes[UPLOAD_QUEUE_BYTES];
} upload_connection_t;
#endif

static struct netif ethernet_netif;
static struct udp_pcb *udp_echo_pcb;
static struct tcp_pcb *tcp_echo_listener;
#if PROJECT_SDCARD_SPI
static struct tcp_pcb *tcp_upload_listener;
static upload_connection_t upload_connection;
#endif
static rx_frame_t rx_queue[ETH_RX_QUEUE_COUNT];
static volatile uint8_t rx_head;
static volatile uint8_t rx_tail;
static uint8_t next_tx_slot;
static uint8_t local_mac[6] = {0x02, 0x20, 0x20, 0x00, 0x00, 0x01};
static uint8_t phy_address = 0xff;
static uint16_t phy_id1;
static uint16_t phy_id2;
static uint16_t phy_status;
static uint16_t phy_control;
static uint16_t phy_partner;
static uint16_t phy_advertisement;
static uint16_t phy_rmii_mode = 0xffffu;
static uint8_t phy_link;
static uint8_t use_dhcp;
static uint8_t stack_ready;
static uint8_t poll_active;
#if PROJECT_SDCARD_SPI
static uint8_t upload_service_active;
#endif
static uint32_t rx_packets;
static uint32_t rx_queue_drops;
static uint32_t rx_bad_frames;
static uint32_t tx_packets;
static uint32_t tx_errors;
static uint32_t rx_hw_errors;
static uint32_t rx_hw_crc_errors;
static uint32_t rx_hw_preamble_errors;
static uint32_t previous_timer;
static uint64_t elapsed_cycles;
static uint64_t last_link_check_ms;
static uint32_t random_state = 0x20a55a17u;

static void rx_capture(void);

static void delay_mdio(void)
{
    volatile unsigned int i;
    for (i = 0; i < 80u; ++i)
        __asm__ volatile ("nop");
}

static void mdio_raw_write(uint32_t word, unsigned int bits)
{
    word <<= 32u - bits;
    while (bits-- != 0) {
        uint32_t output = (word & 0x80000000u) ? MDIO_DO | MDIO_OE : MDIO_OE;
        ethphy_mdio_w_write(output);
        delay_mdio();
        ethphy_mdio_w_write(output | MDIO_CLK);
        delay_mdio();
        ethphy_mdio_w_write(output);
        word <<= 1;
    }
}

static void mdio_turnaround(void)
{
    ethphy_mdio_w_write(0);
    delay_mdio();
    ethphy_mdio_w_write(MDIO_CLK);
    delay_mdio();
    ethphy_mdio_w_write(0);
    delay_mdio();
    ethphy_mdio_w_write(MDIO_CLK);
    delay_mdio();
    ethphy_mdio_w_write(0);
}

static uint16_t mdio_read(uint8_t phy, uint8_t reg)
{
    uint16_t value = 0;
    unsigned int bit;
    ethphy_mdio_w_write(MDIO_OE);
    mdio_raw_write(0xffffffffu, 32);
    mdio_raw_write(1u, 2); /* Clause 22 start */
    mdio_raw_write(2u, 2); /* read */
    mdio_raw_write(phy, 5);
    mdio_raw_write(reg, 5);
    mdio_turnaround();
    for (bit = 0; bit < 16u; ++bit) {
        value = (uint16_t)((value << 1) | ((ethphy_mdio_r_read() & MDIO_DI) != 0));
        ethphy_mdio_w_write(MDIO_CLK);
        delay_mdio();
        ethphy_mdio_w_write(0);
        delay_mdio();
    }
    mdio_turnaround();
    return value;
}

static void mdio_write(uint8_t phy, uint8_t reg, uint16_t value)
{
    ethphy_mdio_w_write(MDIO_OE);
    mdio_raw_write(0xffffffffu, 32);
    mdio_raw_write(1u, 2); /* Clause 22 start */
    mdio_raw_write(1u, 2); /* write */
    mdio_raw_write(phy, 5);
    mdio_raw_write(reg, 5);
    mdio_raw_write(2u, 2); /* driven turnaround: 10 */
    mdio_raw_write(value, 16);
    mdio_turnaround();
}

static void configure_realtek_rmii(uint8_t phy)
{
    /* RTL8201F page 7 register 16: bit 3 selects RMII; bit 12=0 makes
       the PHY supply REF_CLK. The standard Dock routes this clock into A9.
       Preserve the factory TX/RX offsets and restore the management page. */
    uint16_t page = mdio_read(phy, 31);
    mdio_write(phy, 31, 7);
    uint16_t original = mdio_read(phy, 16);
    if (original != 0xffffu) {
        uint16_t configured = (original & ~0x1000u) | 0x0008u;
        if (configured != original)
            mdio_write(phy, 16, configured);
        phy_rmii_mode = mdio_read(phy, 16);
        printf("Ethernet PHY RMII before=%04x after=%04x expected=RMII/PHY-clock-output\n",
               original, phy_rmii_mode);
    } else {
        puts("Ethernet PHY RMII register unavailable; configuration not changed");
    }
    mdio_write(phy, 31, page);
}

unsigned int ethernet_random_u32(void)
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
}

static void update_clock(void)
{
    uint32_t current;
    timer0_update_value_write(1);
    current = timer0_value_read();
    elapsed_cycles += (uint32_t)(previous_timer - current);
    previous_timer = current;
    lwip_sys_set_ms((uint32_t)(elapsed_cycles / (CONFIG_CLOCK_FREQUENCY / 1000u)));
}

static err_t mac_linkoutput(struct netif *netif, struct pbuf *packet)
{
    struct timeout timeout;
    uint8_t slot;
    uint8_t *destination;
    uint16_t length;
    uint32_t copied;
    LWIP_UNUSED_ARG(netif);

    if (packet == NULL || packet->tot_len == 0 || packet->tot_len > ETHMAC_SLOT_SIZE) {
        ++tx_errors;
        return ERR_BUF;
    }
    timeout_start(&timeout, 100000u);
    while (!ethmac_sram_reader_ready_read()) {
        rx_capture();
        if (timeout_expired(&timeout)) {
            ++tx_errors;
            return ERR_TIMEOUT;
        }
    }
    slot = next_tx_slot;
    destination = (uint8_t *)(uintptr_t)(ETHMAC_TX_BASE + (uint32_t)slot * ETHMAC_SLOT_SIZE);
    length = packet->tot_len;
    copied = pbuf_copy_partial(packet, destination, length, 0);
    if (copied != length) {
        ++tx_errors;
        return ERR_BUF;
    }
    ethmac_sram_reader_slot_write(slot);
    ethmac_sram_reader_length_write(length);
    ethmac_sram_reader_start_write(1);
    next_tx_slot = (uint8_t)((slot + 1u) % ETHMAC_TX_SLOTS);
    ++tx_packets;
    return ERR_OK;
}

static err_t ethernet_netif_init(struct netif *netif)
{
    netif->name[0] = 'e';
    netif->name[1] = 'n';
    netif->hwaddr_len = 6;
    memcpy(netif->hwaddr, local_mac, sizeof(local_mac));
    netif->mtu = 1500;
    netif->flags = NETIF_FLAG_BROADCAST | NETIF_FLAG_ETHARP | NETIF_FLAG_ETHERNET;
    netif->output = etharp_output;
    netif->linkoutput = mac_linkoutput;
    netif->hostname = "tang20k";
    return ERR_OK;
}

static void rx_capture(void)
{
    /* A sustained stream must not keep polling forever and starve timers/UART. */
    for (unsigned int captured = 0; captured < ETH_RX_QUEUE_COUNT
            && (ethmac_sram_writer_ev_pending_read() & ETH_EVENT_AVAILABLE); ++captured) {
        uint32_t slot = ethmac_sram_writer_slot_read();
        uint32_t length = ethmac_sram_writer_length_read();
        uint8_t *source;

        if (slot >= ETHMAC_RX_SLOTS || length < 14u || length > ETH_MAX_FRAME) {
            ++rx_bad_frames;
            ethmac_sram_writer_ev_pending_write(ETH_EVENT_AVAILABLE);
            continue;
        }
        source = (uint8_t *)(uintptr_t)(ETHMAC_RX_BASE + slot * ETHMAC_SLOT_SIZE);
        if ((uint8_t)(rx_head - rx_tail) >= ETH_RX_QUEUE_COUNT) {
            ++rx_queue_drops;
        } else {
            rx_frame_t *frame = &rx_queue[rx_head % ETH_RX_QUEUE_COUNT];
            frame->length = (uint16_t)length;
            memcpy(frame->bytes, source, length);
            ++rx_head;
        }
        /* The LiteEth level event consumes one complete frame when cleared. */
        ethmac_sram_writer_ev_pending_write(ETH_EVENT_AVAILABLE);
    }
}

static void udp_echo_received(void *argument, struct udp_pcb *pcb, struct pbuf *packet,
                              const ip_addr_t *address, u16_t port)
{
    LWIP_UNUSED_ARG(argument);
    if (packet == NULL)
        return;
    if (udp_sendto(pcb, packet, address, port) != ERR_OK)
        ++tx_errors;
    pbuf_free(packet);
}

static err_t tcp_echo_received(void *argument, struct tcp_pcb *pcb, struct pbuf *packet,
                               err_t error)
{
    uint8_t *bytes;
    u16_t length;
    err_t result;
    LWIP_UNUSED_ARG(argument);
    if (error != ERR_OK)
        return error;
    if (packet == NULL) {
        if (tcp_close(pcb) != ERR_OK) {
            tcp_abort(pcb);
            return ERR_ABRT;
        }
        return ERR_OK;
    }
    length = packet->tot_len;
    if (tcp_sndbuf(pcb) < length)
        return ERR_MEM;
    bytes = mem_malloc(length);
    if (bytes == NULL)
        return ERR_MEM;
    if (pbuf_copy_partial(packet, bytes, length, 0) != length) {
        mem_free(bytes);
        pbuf_free(packet);
        tcp_abort(pcb);
        return ERR_ABRT;
    }
    result = tcp_write(pcb, bytes, length, TCP_WRITE_FLAG_COPY);
    mem_free(bytes);
    if (result != ERR_OK)
        return result; /* lwIP retains the unconsumed pbuf for retry. */
    tcp_recved(pcb, length);
    pbuf_free(packet);
    (void)tcp_output(pcb); /* Data is queued; output errors are retried by lwIP. */
    return ERR_OK;
}

static err_t tcp_echo_accept(void *argument, struct tcp_pcb *pcb, err_t error)
{
    LWIP_UNUSED_ARG(argument);
    if (error != ERR_OK)
        return error;
    tcp_nagle_disable(pcb);
    tcp_recv(pcb, tcp_echo_received);
    return ERR_OK;
}

static uint32_t read_be32(const uint8_t *bytes)
{
    return ((uint32_t)bytes[0] << 24) | ((uint32_t)bytes[1] << 16)
         | ((uint32_t)bytes[2] << 8) | (uint32_t)bytes[3];
}

static void write_be32(uint8_t *bytes, uint32_t value)
{
    bytes[0] = (uint8_t)(value >> 24);
    bytes[1] = (uint8_t)(value >> 16);
    bytes[2] = (uint8_t)(value >> 8);
    bytes[3] = (uint8_t)value;
}

#if PROJECT_SDCARD_SPI
static void upload_tcp_error(void *argument, err_t error)
{
    upload_connection_t *connection = argument;
    LWIP_UNUSED_ARG(error);
    /* lwIP has already freed the PCB. Defer filesystem cleanup out of callbacks. */
    connection->pcb = NULL;
    connection->abort_pending = 1;
}

static err_t upload_received(void *argument, struct tcp_pcb *pcb,
                             struct pbuf *packet, err_t error)
{
    upload_connection_t *connection = argument;
    uint32_t used, offset, first;
    LWIP_UNUSED_ARG(pcb);
    if (error != ERR_OK)
        return error;
    if (packet == NULL) {
        connection->peer_closed = 1;
        return ERR_OK;
    }
    used = connection->head - connection->tail;
    if (packet->tot_len > UPLOAD_QUEUE_BYTES - used)
        return ERR_MEM; /* Leave the pbuf with lwIP; never overwrite buffered bytes. */
    offset = connection->head % UPLOAD_QUEUE_BYTES;
    first = UPLOAD_QUEUE_BYTES - offset;
    if (first > packet->tot_len)
        first = packet->tot_len;
    if (pbuf_copy_partial(packet, connection->bytes + offset, first, 0) != first
            || (first < packet->tot_len && pbuf_copy_partial(packet, connection->bytes,
                    packet->tot_len - first, first) != packet->tot_len - first)) {
        pbuf_free(packet);
        tcp_abort(pcb);
        return ERR_ABRT;
    }
    connection->head += packet->tot_len;
    pbuf_free(packet);
    /* Advertise more receive window only after the queued bytes are consumed. */
    return ERR_OK;
}

static void upload_reply(upload_connection_t *connection, int success,
                         uint32_t length, uint32_t crc)
{
    connection->response[0] = success ? 'O' : 'E';
    connection->response[1] = success ? 'K' : 'R';
    write_be32(connection->response + 2, length);
    write_be32(connection->response + 6, crc);
    connection->reply_ready = 1;
}

static void upload_service(void)
{
    upload_connection_t *connection = &upload_connection;
    uint32_t count;
    if (upload_service_active || sdcard_app_busy())
        return;
    upload_service_active = 1;
    if (connection->abort_pending || connection->pcb == NULL) {
        if (connection->active)
            sdcard_upload_abort(&connection->file);
        memset(connection, 0, sizeof(*connection));
        goto done;
    }
    if (!connection->reply_ready) {
        while (connection->header_bytes < sizeof(connection->header)
                && connection->tail != connection->head) {
            connection->header[connection->header_bytes++] =
                connection->bytes[connection->tail++ % UPLOAD_QUEUE_BYTES];
            tcp_recved(connection->pcb, 1);
        }
        if (connection->header_bytes == sizeof(connection->header) && !connection->active) {
            int success = sdcard_upload_begin(&connection->file,
                read_be32(connection->header), read_be32(connection->header + 4));
            connection->active = success != 0;
            if (!success)
                upload_reply(connection, 0, 0, 0);
        }
        while (connection->active && connection->tail != connection->head
                && connection->pcb != NULL) {
            uint32_t offset = connection->tail % UPLOAD_QUEUE_BYTES;
            count = connection->head - connection->tail;
            if (count > 512u) count = 512u;
            if (count > UPLOAD_QUEUE_BYTES - offset) count = UPLOAD_QUEUE_BYTES - offset;
            if (!sdcard_upload_write(&connection->file, connection->bytes + offset, count)) {
                sdcard_upload_abort(&connection->file);
                connection->active = 0;
                upload_reply(connection, 0, 0, 0);
                break;
            }
            connection->tail += count;
            if (connection->pcb != NULL)
                tcp_recved(connection->pcb, (u16_t)count);
        }
        if (connection->active && connection->pcb != NULL
                && connection->file.received_bytes == connection->file.expected_bytes) {
            uint32_t crc = 0, length = 0;
            int success = sdcard_upload_finish(&connection->file, &crc, &length);
            connection->active = 0;
            upload_reply(connection, success, length, crc);
        } else if (connection->peer_closed && connection->tail == connection->head
                && !connection->reply_ready) {
            if (connection->active)
                sdcard_upload_abort(&connection->file);
            connection->active = 0;
            upload_reply(connection, 0, 0, 0);
        }
    }
    if (connection->pcb != NULL && connection->reply_ready) {
        err_t result = ERR_OK;
        if (!connection->reply_sent) {
            result = tcp_write(connection->pcb, connection->response,
                               sizeof(connection->response), TCP_WRITE_FLAG_COPY);
            if (result == ERR_OK) {
                connection->reply_sent = 1;
                (void)tcp_output(connection->pcb);
            }
        }
        if (result == ERR_OK) {
            struct tcp_pcb *pcb = connection->pcb;
            tcp_arg(pcb, NULL);
            tcp_err(pcb, NULL);
            tcp_recv(pcb, NULL);
            result = tcp_close(pcb);
            if (result == ERR_MEM) {
                tcp_arg(pcb, connection);
                tcp_err(pcb, upload_tcp_error);
                tcp_recv(pcb, upload_received);
            } else {
                if (result != ERR_OK)
                    tcp_abort(pcb);
                connection->pcb = NULL;
            }
        }
    }
done:
    upload_service_active = 0;
}

static err_t upload_tcp_accept(void *argument, struct tcp_pcb *pcb, err_t error)
{
    LWIP_UNUSED_ARG(argument);
    if (error != ERR_OK)
        return error;
    if (upload_connection.pcb != NULL || upload_connection.active
            || upload_connection.abort_pending || upload_service_active) {
        tcp_abort(pcb);
        return ERR_ABRT;
    }
    memset(&upload_connection, 0, sizeof(upload_connection));
    upload_connection.pcb = pcb;
    tcp_arg(pcb, &upload_connection);
    tcp_recv(pcb, upload_received);
    tcp_err(pcb, upload_tcp_error);
    tcp_nagle_disable(pcb);
    return ERR_OK;
}

static void sd_network_poll(void)
{
    update_clock();
    rx_capture();
    if (!poll_active)
        ethernet_app_poll();
}
#endif

static void udp_create(void)
{
    err_t result;
    udp_echo_pcb = udp_new();
    if (udp_echo_pcb == NULL) {
        puts("Ethernet: cannot allocate UDP echo PCB");
        return;
    }
    result = udp_bind(udp_echo_pcb, IP_ADDR_ANY, ETH_UDP_ECHO_PORT);
    if (result != ERR_OK) {
        udp_remove(udp_echo_pcb);
        udp_echo_pcb = NULL;
        printf("Ethernet: UDP echo bind failed (%d)\n", (int)result);
        return;
    }
    udp_recv(udp_echo_pcb, udp_echo_received, NULL);
}

static struct tcp_pcb *tcp_listen_on(uint16_t port, tcp_accept_fn accept_callback)
{
    struct tcp_pcb *pcb = tcp_new();
    err_t result;
    if (pcb == NULL)
        return NULL;
    result = tcp_bind(pcb, IP_ADDR_ANY, port);
    if (result != ERR_OK) {
        tcp_abort(pcb);
        return NULL;
    }
    struct tcp_pcb *listener = tcp_listen_with_backlog(pcb, TCP_DEFAULT_LISTEN_BACKLOG);
    if (listener == NULL) {
        tcp_abort(pcb);
        return NULL;
    }
    pcb = listener;
    tcp_accept(pcb, accept_callback);
    return pcb;
}

static void update_link(void)
{
    uint64_t now = elapsed_cycles / (CONFIG_CLOCK_FREQUENCY / 1000u);
    uint8_t address;
    uint16_t status;
    if (now - last_link_check_ms < 500u)
        return;
    last_link_check_ms = now;
    if (phy_address == 0xffu) {
        for (address = 0; address < 32u; ++address) {
            uint16_t id1 = mdio_read(address, 2);
            uint16_t id2 = mdio_read(address, 3);
            if (id1 != 0u && id1 != 0xffffu && id2 != 0u && id2 != 0xffffu) {
                phy_address = address;
                phy_id1 = id1;
                phy_id2 = id2;
                break;
            }
        }
        if (phy_address == 0xffu)
            return;
        printf("Ethernet PHY addr=%u id=%04x:%04x\n", phy_address, phy_id1, phy_id2);
        if (phy_id1 == 0x001cu && phy_id2 == 0xc816u)
            configure_realtek_rmii(phy_address);
    }
    (void)mdio_read(phy_address, 1); /* BMSR link bit is latched low. */
    status = mdio_read(phy_address, 1);
    phy_status = status;
    phy_partner = mdio_read(phy_address, 5);
    phy_advertisement = mdio_read(phy_address, 4);
    phy_control = mdio_read(phy_address, 0);
    phy_link = (status & (1u << 2)) != 0;
    if (phy_link && !netif_is_link_up(&ethernet_netif))
        netif_set_link_up(&ethernet_netif);
    else if (!phy_link && netif_is_link_up(&ethernet_netif))
        netif_set_link_down(&ethernet_netif);
}

static const char *link_mode(void)
{
    if (!phy_link)
        return "down";
    if ((phy_status & (1u << 5)) == 0)
        return "up (autonegotiation pending)";
    if ((phy_control & (1u << 12)) != 0) {
        uint16_t common = phy_advertisement & phy_partner;
        if (common & (1u << 8)) return "100M-full";
        if (common & (1u << 7)) return "100M-half";
        if (common & (1u << 6)) return "10M-full";
        if (common & (1u << 5)) return "10M-half";
        return "up (negotiated mode unavailable)";
    }
    if ((phy_control & (1u << 13)) != 0)
        return (phy_control & (1u << 8)) ? "100M-full" : "100M-half";
    return (phy_control & (1u << 8)) ? "10M-full" : "10M-half";
}

void ethernet_app_init(void)
{
    ip4_addr_t ip, mask, gateway;

#if PROJECT_SDCARD_SPI
    memset(&upload_connection, 0, sizeof(upload_connection));
#endif
    rx_head = rx_tail = 0;
    next_tx_slot = 0;
    ethmac_sram_writer_ev_pending_write(ETH_EVENT_AVAILABLE);
    ethmac_sram_reader_ev_pending_write(ETH_EVENT_AVAILABLE);
    ethmac_sram_writer_ev_enable_write(0);
    ethmac_sram_reader_ev_enable_write(0);
    ethphy_crg_reset_write(1);
    for (volatile unsigned int i = 0; i < 960000u; ++i)
        __asm__ volatile ("nop");
    ethphy_crg_reset_write(0);
    for (volatile unsigned int i = 0; i < 960000u; ++i)
        __asm__ volatile ("nop");

    timer0_update_value_write(1);
    previous_timer = timer0_value_read();
    elapsed_cycles = 0;
    lwip_sys_set_ms(0);
    lwip_init();
    IP4_ADDR(&ip, 0, 0, 0, 0);
    IP4_ADDR(&mask, 0, 0, 0, 0);
    IP4_ADDR(&gateway, 0, 0, 0, 0);
    if (netif_add(&ethernet_netif, &ip, &mask, &gateway, NULL,
                  ethernet_netif_init, ethernet_input) == NULL) {
        puts("Ethernet: netif allocation failed");
        return;
    }
    netif_set_default(&ethernet_netif);
    netif_set_down(&ethernet_netif);
    udp_create();
    tcp_echo_listener = tcp_listen_on(ETH_TCP_ECHO_PORT, tcp_echo_accept);
#if PROJECT_SDCARD_SPI
    tcp_upload_listener = tcp_listen_on(ETH_TCP_UPLOAD_PORT, upload_tcp_accept);
#endif
    printf("Ethernet services: UDP echo %u, TCP echo %u, SD upload %u\n",
           ETH_UDP_ECHO_PORT, ETH_TCP_ECHO_PORT,
#if PROJECT_SDCARD_SPI
           ETH_TCP_UPLOAD_PORT
#else
           0u
#endif
           );
    if (tcp_echo_listener == NULL
#if PROJECT_SDCARD_SPI
            || tcp_upload_listener == NULL
#endif
            )
        puts("Ethernet: a TCP service could not bind; check lwIP memory settings");

#if PROJECT_SDCARD_SPI
    sd_spi_set_poll_hook(sd_network_poll);
#endif
    stack_ready = 1;
    update_clock();
    update_link();
}

void ethernet_app_poll(void)
{
    unsigned int budget = ETH_RX_QUEUE_COUNT;
    if (!stack_ready || poll_active)
        return;
    poll_active = 1;
    update_clock();
    rx_capture();
    update_link();
    while (rx_tail != rx_head && budget-- != 0) {
        rx_frame_t *frame = &rx_queue[rx_tail % ETH_RX_QUEUE_COUNT];
        struct pbuf *packet = pbuf_alloc(PBUF_RAW, frame->length, PBUF_POOL);
        if (packet == NULL) {
            ++rx_queue_drops;
        } else {
            err_t result = pbuf_take(packet, frame->bytes, frame->length);
            if (result != ERR_OK || ethernet_input(packet, &ethernet_netif) != ERR_OK)
                pbuf_free(packet);
            else
                ++rx_packets;
        }
        ++rx_tail;
        rx_capture();
    }
    sys_check_timeouts();
    rx_hw_errors = ethmac_sram_writer_errors_read();
    rx_hw_crc_errors = ethmac_rx_datapath_crc_errors_read();
    rx_hw_preamble_errors = ethmac_rx_datapath_preamble_errors_read();
    poll_active = 0;
#if PROJECT_SDCARD_SPI
    upload_service();
#endif
}

static void ethernet_app_stop(void)
{
    stack_ready = 0;
    if (use_dhcp)
        dhcp_stop(&ethernet_netif);
    netif_set_down(&ethernet_netif);
    if (udp_echo_pcb != NULL) {
        udp_remove(udp_echo_pcb);
        udp_echo_pcb = NULL;
    }
    if (tcp_echo_listener != NULL) {
        (void)tcp_close(tcp_echo_listener);
        tcp_echo_listener = NULL;
    }
#if PROJECT_SDCARD_SPI
    if (tcp_upload_listener != NULL) {
        (void)tcp_close(tcp_upload_listener);
        tcp_upload_listener = NULL;
    }
#endif
    /* This NO_SYS application owns every active TCP PCB. Aborting invokes
       error callbacks, then upload_service performs deferred sync/close. */
    while (tcp_active_pcbs != NULL)
        tcp_abort(tcp_active_pcbs);
#if PROJECT_SDCARD_SPI
    upload_connection.abort_pending = 1;
    upload_connection.pcb = NULL;
    upload_service();
#endif
    puts("Ethernet stopped; upload file closed; SRAM reload required to restart services");
}

static int hex_value(char character)
{
    if (character >= '0' && character <= '9')
        return character - '0';
    if (character >= 'a' && character <= 'f')
        return character - 'a' + 10;
    if (character >= 'A' && character <= 'F')
        return character - 'A' + 10;
    return -1;
}

static int parse_mac(const char *text, uint8_t mac[6])
{
    unsigned int i;
    for (i = 0; i < 6u; ++i) {
        if (text[0] == '\0' || text[1] == '\0')
            return 0;
        int high = hex_value(text[0]);
        int low = hex_value(text[1]);
        if (high < 0 || low < 0)
            return 0;
        mac[i] = (uint8_t)((high << 4) | low);
        text += 2;
        if (i < 5u) {
            if (*text != ':')
                return 0;
            ++text;
        }
    }
    return *text == '\0' && (mac[0] & 3u) == 2u;
}

static int next_word(const char **cursor, char *output, size_t capacity)
{
    size_t length = 0;
    while (**cursor == ' ' || **cursor == '\t')
        ++*cursor;
    while (**cursor != '\0' && **cursor != ' ' && **cursor != '\t') {
        if (length + 1u >= capacity)
            return 0;
        output[length++] = **cursor;
        ++*cursor;
    }
    if (length == 0)
        return 0;
    output[length] = '\0';
    while (**cursor == ' ' || **cursor == '\t')
        ++*cursor;
    return 1;
}

static void print_status(void)
{
    char ip_text[16], mask_text[16], gateway_text[16];
    ip4addr_ntoa_r(netif_ip4_addr(&ethernet_netif), ip_text, sizeof(ip_text));
    ip4addr_ntoa_r(netif_ip4_netmask(&ethernet_netif), mask_text, sizeof(mask_text));
    ip4addr_ntoa_r(netif_ip4_gw(&ethernet_netif), gateway_text, sizeof(gateway_text));
    printf("Ethernet: phy=%s", phy_address == 0xffu ? "not found" : "identified");
    if (phy_address != 0xffu)
        printf(" addr=%u id=%04x:%04x link=%s bmsr=%04x bmcr=%04x anar=%04x lpa=%04x rmii=%04x",
               phy_address, phy_id1, phy_id2, link_mode(), phy_status,
               phy_control, phy_advertisement, phy_partner, phy_rmii_mode);
    printf("\nMAC=%02x:%02x:%02x:%02x:%02x:%02x\n",
           local_mac[0], local_mac[1], local_mac[2], local_mac[3], local_mac[4], local_mac[5]);
    printf("IPv4=%s mask=%s gateway=%s mode=%s\n", ip_text, mask_text, gateway_text,
           use_dhcp ? (dhcp_supplied_address(&ethernet_netif) ? "DHCP leased" : "DHCP pending")
                    : "static/unconfigured");
    printf("frames rx=%lu tx=%lu rx_queue_drop=%lu rx_bad=%lu tx_error=%lu hw_drop=%lu crc=%lu preamble=%lu\n",
           (unsigned long)rx_packets, (unsigned long)tx_packets,
           (unsigned long)rx_queue_drops, (unsigned long)rx_bad_frames,
           (unsigned long)tx_errors, (unsigned long)rx_hw_errors,
           (unsigned long)rx_hw_crc_errors, (unsigned long)rx_hw_preamble_errors);
}

int ethernet_app_command(const char *line)
{
    if (strcmp(line, "net restart") == 0) {
        if (phy_address == 0xffu) {
            puts("Ethernet restart refused: no identified PHY");
            return 1;
        }
        uint16_t advertisement = mdio_read(phy_address, 4);
        uint16_t control = mdio_read(phy_address, 0);
        if ((advertisement & 0x01e0u) == 0) {
            advertisement = 0x01e1u; /* IEEE 802.3 selector, 10/100 half/full. */
            mdio_write(phy_address, 4, advertisement);
        }
        /* Enable/restart AN, clearing power-down, isolation and loopback. */
        mdio_write(phy_address, 0, (control & 0x2100u) | 0x1200u);
        printf("Ethernet negotiation restarted: bmcr=%04x anar=%04x\n",
               mdio_read(phy_address, 0), mdio_read(phy_address, 4));
        return 1;
    }
    if (strcmp(line, "net stop") == 0) {
        ethernet_app_stop();
        return 1;
    }
    if (strcmp(line, "net status") == 0) {
        print_status();
        return 1;
    }
    if (strncmp(line, "net mac ", 8) == 0) {
        uint8_t replacement[6];
        if (!parse_mac(line + 8, replacement)) {
            puts("Usage: net mac XX:XX:XX:XX:XX:XX (locally administered unicast address)");
            return 1;
        }
        memcpy(local_mac, replacement, sizeof(local_mac));
        memcpy(ethernet_netif.hwaddr, local_mac, sizeof(local_mac));
        etharp_cleanup_netif(&ethernet_netif);
        puts("Ethernet MAC updated");
        return 1;
    }
    if (strncmp(line, "net static ", 11) == 0) {
        char ip_text[16], mask_text[16], gateway_text[16];
        const char *cursor = line + 11;
        ip4_addr_t ip, mask, gateway;
        if (!next_word(&cursor, ip_text, sizeof(ip_text))
                || !next_word(&cursor, mask_text, sizeof(mask_text))
                || !next_word(&cursor, gateway_text, sizeof(gateway_text))
                || *cursor != '\0'
                || !ip4addr_aton(ip_text, &ip) || !ip4addr_aton(mask_text, &mask)
                || !ip4addr_aton(gateway_text, &gateway)) {
            puts("Usage: net static IP MASK GATEWAY");
            return 1;
        }
        if (use_dhcp)
            (void)dhcp_stop(&ethernet_netif);
        use_dhcp = 0;
        netif_set_addr(&ethernet_netif, &ip, &mask, &gateway);
        netif_set_up(&ethernet_netif);
        printf("Static IPv4 configured: %s/%s gateway %s\n", ip_text, mask_text, gateway_text);
        return 1;
    }
    if (strcmp(line, "net dhcp") == 0) {
        err_t result;
        use_dhcp = 1;
        netif_set_addr(&ethernet_netif, IP4_ADDR_ANY4, IP4_ADDR_ANY4, IP4_ADDR_ANY4);
        netif_set_up(&ethernet_netif);
        result = dhcp_start(&ethernet_netif);
        if (result == ERR_OK)
            puts("DHCP started; use net status to check for a lease");
        else
            printf("DHCP start failed (%d); retry with net dhcp after checking link\n", (int)result);
        return 1;
    }
    if (strncmp(line, "net ", 4) == 0) {
        puts("Usage: net status | net restart | net mac XX:XX:XX:XX:XX:XX | net static IP MASK GATEWAY | net dhcp | net stop");
        return 1;
    }
    return 0;
}
