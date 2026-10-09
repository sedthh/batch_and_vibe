// Coordinate wire payload and map checks. No engine pointers supplied by clients.
#pragma once
#include "movement_guard.h"
namespace bwv_probe {
struct PositionDestination { float x=0,z=0; };
static_assert(sizeof(PositionDestination)==8);
inline bool valid_destination(const PositionDestination& p,const Movement& m) {
    return m.verified && world_point(p.x,p.z,m.width,m.depth);
}
// Called only after the request-bound identity has been revalidated.
template<class Read>
bool resolve_position_target(uintptr_t object,const Movement& map,Read read,PositionDestination& out) {
    int32_t x=0,z=0;
    out={};
    if(!object || !read(object+0x14,x) || !read(object+0x18,z))return false;
    const PositionDestination current{float(x*(10./65536.)),float(z*(10./65536.))};
    if(!valid_destination(current,map))return false;
    out=current;return true;
}
}
