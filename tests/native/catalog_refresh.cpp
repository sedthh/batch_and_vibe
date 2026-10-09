#include "check.h"
#include "../../native/catalog_refresh.h"
int main() {
    using bwv_probe::CatalogRefresh;
    CatalogRefresh schedule;
    unsigned scans=0;
    // A minute at 1000 hooks/s still produces only two catalog scans/second.
    for (uint64_t ms=0;ms<60000;++ms)
        scans+=schedule.due(ms,1,10,20,0,0,ms,true,false);
    assert(scans==120);
    assert(schedule.due(60000,1,10,20,0,0,60000,true,true));
    assert(schedule.due(60001,1,10,20,0,123,60001,true,false)); // new lookup
    assert(!schedule.due(60002,1,10,20,0,123,60002,true,false));
    assert(schedule.due(60003,1,10,20,40,123,60003,true,false)); // held object
    assert(schedule.due(60004,2,10,20,40,123,60004,true,false)); // new game
    assert(schedule.due(60005,2,11,21,40,123,60005,true,false)); // new creature
    assert(schedule.due(60006,2,11,21,40,123,1,true,false)); // simulation reset
    assert(schedule.due(60007,2,11,21,40,0,2,true,false)); // expired lookup
    assert(schedule.due(61000,2,11,21,40,0,3,false,false));
    assert(schedule.due(61001,2,11,21,40,0,4,true,false)); // resume
    assert(schedule.due(1,2,11,21,40,0,5,true,false)); // clock reset
}
