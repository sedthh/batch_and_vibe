// Existing same-island gates. Reading the network never creates a belief or acts.
#pragma once
#include <cstdint>
#include <cstddef>
#include <cmath>
namespace bwv_probe {
struct TeleportGate {
    uintptr_t object=0,table=0;
    int32_t x=0,z=0;float altitude=0;
};
struct TeleportRoute { TeleportGate entrance,destination; };
// ProcessTeleports removes a traveller's saved destination unless its active
// reaction is this entrance's reaction. A walking agenda alone is insufficient.
template<class Read,class Type>
uintptr_t teleport_reaction(uintptr_t entrance,Read read,Type type) {
    uint32_t reaction=0,source=0,kind=0;
    if(!type(entrance,".?AVMagicTeleport@@") || !read(entrance+0x94,reaction)
            || !type(reaction,".?AVReaction@@") || !read(reaction+0x14,source)
            || source!=entrance || !read(reaction+0x24,kind) || kind!=20)return 0;
    return reaction;
}
struct TeleportNetwork {
    static constexpr size_t capacity=32;
    TeleportGate gates[capacity]{};size_t count=0;
    bool route(uintptr_t destination,double x,double z,TeleportRoute& out) const {
        out={};
        for(size_t i=0;i<count;++i)if(gates[i].object==destination)out.destination=gates[i];
        if(!out.destination.object || !std::isfinite(x) || !std::isfinite(z))return false;
        double best=INFINITY;
        for(size_t i=0;i<count;++i) {
            const auto& gate=gates[i];if(gate.object==destination)continue;
            const double distance=std::hypot(gate.x*(10./65536.)-x,gate.z*(10./65536.)-z);
            if(distance<best || (distance==best && gate.object<out.entrance.object)) {
                best=distance;out.entrance=gate;
            }
        }
        return out.entrance.object!=0;
    }
};
template<class Read,class Type>
bool read_teleport_network(uintptr_t player,double width,double depth,Read read,Type type,TeleportNetwork& out) {
    out={};uint32_t first=0,count=0;
    if(!type(player,".?AVGPlayer@@") || !read(player+0xA58,first) || !read(player+0xA5C,count)
            || count>TeleportNetwork::capacity || !(width>0 && depth>0))return false;
    TeleportNetwork result;uintptr_t node=first;
    for(uint32_t i=0;i<count;++i) {
        for(size_t j=0;j<result.count;++j)if(result.gates[j].object==node)return false;
        TeleportGate gate;gate.object=node;uint32_t table=0,owner=0,next=0;
        if(!node || !type(node,".?AVMagicTeleport@@") || !read(node,table)
                || !read(node+0xA0,owner) || owner!=player || !read(node+0x88,next)
                || !read(node+0x14,gate.x) || !read(node+0x18,gate.z) || !read(node+0x1C,gate.altitude))return false;
        const double x=gate.x*(10./65536.),z=gate.z*(10./65536.);
        if(!table || !std::isfinite(gate.altitude) || !(x>0 && z>0 && x<width && z<depth))return false;
        gate.table=table;result.gates[result.count++]=gate;node=next;
    }
    uint32_t after_first=0,after_count=0;
    if(node || !read(player+0xA58,after_first) || !read(player+0xA5C,after_count)
            || first!=after_first || count!=after_count)return false;
    out=result;return true;
}
}
