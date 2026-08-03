/* Ground-truth "hello" sample.
 *
 * Deliberately more than main(): a handful of distinct functions with real
 * bodies (loop, recursion, libc calls) so function discovery, symbol tables,
 * and O0-vs-O2 code entropy have something to chew on. Compiled at -O0, -O2,
 * -O2 static, stripped, and UPX-packed by corpus/build.py.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checksum(const unsigned char *buf, size_t n) {
    unsigned h = 2166136261u;
    for (size_t i = 0; i < n; i++) {
        h ^= buf[i];
        h *= 16777619u;
    }
    return h;
}

static unsigned long factorial(unsigned n) {
    if (n < 2)
        return 1;
    return (unsigned long)n * factorial(n - 1);
}

static int compare_ints(const void *a, const void *b) {
    return *(const int *)a - *(const int *)b;
}

static void fill_and_sort(int *vals, size_t n, unsigned seed) {
    unsigned state = seed;
    for (size_t i = 0; i < n; i++) {
        state = state * 1103515245u + 12345u;
        vals[i] = (int)(state >> 16);
    }
    qsort(vals, n, sizeof vals[0], compare_ints);
}

static void report(const char *label, unsigned long value) {
    printf("%s: %lu\n", label, value);
}

int main(int argc, char **argv) {
    const char *name = argc > 1 ? argv[1] : "world";
    printf("hello, %s\n", name);

    report("factorial(10)", factorial(10));
    report("name checksum", checksum((const unsigned char *)name, strlen(name)));

    int vals[64];
    fill_and_sort(vals, 64, (unsigned)argc * 2654435761u);
    report("median", (unsigned long)(unsigned)vals[32]);

    return 0;
}
