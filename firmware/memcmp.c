#include <stddef.h>
#include <string.h>

int memcmp(const void *left, const void *right, size_t length)
{
    const unsigned char *a = left;
    const unsigned char *b = right;
    size_t i;
    for (i = 0; i < length; ++i) {
        if (a[i] != b[i])
            return (int)a[i] - (int)b[i];
    }
    return 0;
}
