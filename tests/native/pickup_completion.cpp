#include "../../native/pickup_completion.h"
#include "check.h"
int main() {
    bwv_probe::PickupCompletion p;
    assert(!p.waiting(100,false,false));
    assert(p.waiting(200,true,false));
    assert(p.waiting(2100,true,false));
    assert(!p.waiting(2200,true,false)); // bounded, no reset from repeated exits
    p.reset();
    assert(p.waiting(3000,true,false));
    assert(!p.waiting(3100,true,true)); // late held-object evidence ends waiting
}
