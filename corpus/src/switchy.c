/* Jump-table sample: a dense 20-case switch that -O2 lowers to an indirect
 * jump through a table. Each case has a distinct side effect so the compiler
 * cannot collapse the switch into arithmetic or a constant lookup.
 *
 * Phase 5 acceptance: the dispatch either resolves all 20 targets from the
 * table, or emits exactly one `unresolved` record — never a truncated CFG.
 */

#include <stdio.h>
#include <stdlib.h>

static volatile unsigned sink;

static void dispatch(int op, unsigned arg) {
    switch (op) {
    case 0:  printf("op0 add %u\n", arg + 1); break;
    case 1:  printf("op1 sub %u\n", arg - 1); break;
    case 2:  printf("op2 mul %u\n", arg * 3); break;
    case 3:  printf("op3 div %u\n", arg / 3u + 1u); break;
    case 4:  printf("op4 mod %u\n", arg % 7u); break;
    case 5:  printf("op5 shl %u\n", arg << 2); break;
    case 6:  printf("op6 shr %u\n", arg >> 2); break;
    case 7:  printf("op7 xor %u\n", arg ^ 0xA5A5u); break;
    case 8:  printf("op8 and %u\n", arg & 0x0F0Fu); break;
    case 9:  printf("op9 or  %u\n", arg | 0x1000u); break;
    case 10: printf("op10 neg %u\n", ~arg); break;
    case 11: printf("op11 rot %u\n", (arg << 5) | (arg >> 27)); break;
    case 12: printf("op12 sq  %u\n", arg * arg); break;
    case 13: printf("op13 dbl %u\n", arg + arg); break;
    case 14: printf("op14 hi  %u\n", arg >> 16); break;
    case 15: printf("op15 lo  %u\n", arg & 0xFFFFu); break;
    case 16: printf("op16 rev %u\n", ((arg & 0xFFu) << 8) | ((arg >> 8) & 0xFFu)); break;
    case 17: printf("op17 par %u\n", __builtin_popcount(arg) & 1u); break;
    case 18: printf("op18 clz %u\n", arg ? (unsigned)__builtin_clz(arg) : 32u); break;
    case 19: printf("op19 mix %u\n", arg * 2654435761u); break;
    default: printf("op? %d\n", op); break;
    }
    sink = arg;
}

int main(int argc, char **argv) {
    unsigned arg = argc > 2 ? (unsigned)strtoul(argv[2], NULL, 0) : 42u;
    int op = argc > 1 ? atoi(argv[1]) : 0;
    dispatch(op, arg);
    for (int i = 0; i < 20; i++)
        dispatch(i, arg + (unsigned)i);
    return 0;
}
