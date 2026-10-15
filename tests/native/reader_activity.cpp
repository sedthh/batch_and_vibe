#include "check.h"
#include "../../native/reader_activity.h"
int main(){
    bwv_observer::ReaderActivity reader;
    assert(!reader.active(0));assert(!reader.active(100000));
    assert(reader.touch(0));assert(reader.active(0));
    assert(reader.active(2999));assert(!reader.active(3000));
    assert(reader.touch(3000));assert(!reader.touch(4000));
    assert(reader.active(6999));assert(!reader.active(7000));
    assert(!reader.active(3999)); // Clock rollback cannot extend the lease.
    assert(reader.touch(5));assert(reader.active(5));
    for(uint64_t now=100;now<60000;now+=250){reader.touch(now);assert(reader.active(now));}
    assert(!reader.active(70000));
}
