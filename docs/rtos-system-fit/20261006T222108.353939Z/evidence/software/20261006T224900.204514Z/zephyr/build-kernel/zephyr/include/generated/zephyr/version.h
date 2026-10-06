#ifndef _KERNEL_VERSION_H_
#define _KERNEL_VERSION_H_

/* The template values come from cmake/modules/version.cmake
 * BUILD_VERSION related template values will be 'git describe',
 * alternatively user defined BUILD_VERSION.
 */

#define ZEPHYR_VERSION_CODE 263169
#define ZEPHYR_VERSION(a,b,c) (((a) << 16) + ((b) << 8) + (c))

#define KERNELVERSION                   0x4040100
#define KERNEL_VERSION_NUMBER           0x40401
#define KERNEL_VERSION_MAJOR            4
#define KERNEL_VERSION_MINOR            4
#define KERNEL_PATCHLEVEL               1
#define KERNEL_TWEAK                    0
#define KERNEL_EXTRAVERSION             
#define KERNEL_VERSION_STRING           "4.4.1"
#define KERNEL_VERSION_EXTENDED_STRING  "4.4.1+0"
#define KERNEL_VERSION_TWEAK_STRING     "4.4.1+0"

#define BUILD_VERSION v4.4.1


#endif /* _KERNEL_VERSION_H_ */
