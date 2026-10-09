#pragma once
#include <cstdio>
#include <cstdlib>
// Always active, including optimized builds, with a useful CLI failure message.
#ifdef assert
#undef assert
#endif
#define assert(expression) do { if(!(expression)) { \
    std::fprintf(stderr,"%s:%d: check failed: %s\n",__FILE__,__LINE__,#expression); \
    std::exit(1); } } while(false)
