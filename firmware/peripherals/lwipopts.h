#ifndef TANG20K_LWIPOPTS_H
#define TANG20K_LWIPOPTS_H

unsigned int ethernet_random_u32(void);

/* Small, single-threaded IPv4 configuration for the 48 MHz bare-metal CPU. */
#define NO_SYS                         1
#define SYS_LIGHTWEIGHT_PROT           0
#define MEM_ALIGNMENT                  4
#define MEM_SIZE                       (48 * 1024)
#define MEMP_NUM_PBUF                  20
#define MEMP_NUM_UDP_PCB               4
#define MEMP_NUM_TCP_PCB               6
#define MEMP_NUM_TCP_PCB_LISTEN        2
#define MEMP_NUM_TCP_SEG               48
#define PBUF_POOL_SIZE                 12
#define PBUF_POOL_BUFSIZE              1600

#define LWIP_IPV4                      1
#define LWIP_IPV6                      0
#define LWIP_ARP                       1
#define LWIP_ICMP                      1
#define LWIP_RAW                       0
#define LWIP_UDP                       1
#define LWIP_TCP                       1
#define LWIP_DHCP                      1
#define LWIP_AUTOIP                    0
#define LWIP_DNS                       0
#define LWIP_IGMP                      0
#define LWIP_ETHERNET                  1
#define LWIP_NETIF_API                 0
#define LWIP_NETCONN                   0
#define LWIP_SOCKET                    0
#define LWIP_NETIF_LINK_CALLBACK       1
#define LWIP_NETIF_HOSTNAME            1
#define LWIP_TIMERS                    1
#define LWIP_TIMERS_CUSTOM             0
#define LWIP_STATS                     0
#define LWIP_DEBUG                     0

#define ETH_PAD_SIZE                   0
#define TCP_MSS                        1460
#define TCP_WND                        (8 * TCP_MSS)
#define TCP_SND_BUF                    (8 * TCP_MSS)
#define TCP_SND_QUEUELEN               48
#define TCP_QUEUE_OOSEQ                0
#define TCP_LISTEN_BACKLOG             1
#define LWIP_TCP_KEEPALIVE             1
#define LWIP_TCP_SACK_OUT              0
#define LWIP_CHECKSUM_CTRL_PER_NETIF   0
#define LWIP_CHKSUM_ALGORITHM          3

#define LWIP_PROVIDE_ERRNO             1
#define LWIP_RAND()                    ethernet_random_u32()

#endif
