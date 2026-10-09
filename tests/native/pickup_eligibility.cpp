#include "../../native/pickup_eligibility.h"
#include "check.h"
#include <cstring>
#include <limits>
using namespace bwv_probe;
int main() {
    assert(!pickup_limit_reason(2,3,5,10,10));
    assert(!std::strcmp(pickup_limit_reason(2,4,5,10,10),"too_large_for_current_size"));
    assert(!std::strcmp(pickup_limit_reason(2,3,5,11,10),"too_heavy_to_lift"));
    assert(!pickup_limit_reason(-1,3,5,11,10));
    assert(!pickup_limit_reason(2,3,std::numeric_limits<double>::quiet_NaN(),11,10));
}
