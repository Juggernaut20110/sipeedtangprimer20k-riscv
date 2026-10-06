#ifndef TANG20K_ETHERNET_LWIP_H
#define TANG20K_ETHERNET_LWIP_H

void ethernet_app_init(void);
void ethernet_app_poll(void);
int ethernet_app_command(const char *line);

#endif
