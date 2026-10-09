// Original adapter for the native point argument and movement subaction.
// Uses only engine-owned callbacks/arguments, never a mod pointer in the agenda.
#pragma once
namespace bwv_probe {
inline bool position_contract() {
    return word(0x9D1698+80)==0x49B010 // MoveToPos plan constructor
        && word(0x8CEFEC)==0x4FF820 // SubArgumentPointAndFloat copy
        && range(0x4FF240,1) && range(0x4FF3A0,1) && range(0x4D0C80,1)
        && range(0x803090,1) && range(0x7DB790,1);
}
inline bool append_position_movement(uintptr_t creature,uintptr_t mental,
        const PositionDestination& destination) {
    const uintptr_t sequence=mental+0xFA8;
    if(!position_contract() || !type(sequence,".?AVCreatureSubActionAgenda@@")
            || word(sequence+0xC28)!=creature || word(sequence+0x14)!=0
            || word(mental+0xF60)!=1) return false;
    struct MapPoint {int32_t x,z;float altitude;};
    MapPoint map{static_cast<int32_t>(destination.x*(65536./10.)),
                 static_cast<int32_t>(destination.z*(65536./10.)),0};
    using GroundHeight=float(__thiscall*)(void*);
    const float y=reinterpret_cast<GroundHeight>(0x803090)(&map);
    if(!std::isfinite(y))return false;
    struct PointArgument {uintptr_t table;float x,y,z,stop_distance;};
    static_assert(sizeof(PointArgument)==20);
    using Allocate=void*(__cdecl*)(size_t,const char*,int);
    auto* argument=static_cast<PointArgument*>(reinterpret_cast<Allocate>(0x7DB790)(
        sizeof(PointArgument),"bwv-position",0));
    if(!argument)return false;
    *argument={0x8CEFEC,destination.x,y,destination.z,2.f};
    struct Function {uintptr_t address;int32_t adjust,index,virtual_offset;};
    using Append=void(__thiscall*)(void*,uint32_t,void*,Function,Function);
    // Subaction 8 uses the stock location callback reading its copied XYZ
    // argument. Append copies/frees the temporary argument through the engine.
    reinterpret_cast<Append>(0x4FF3A0)(reinterpret_cast<void*>(sequence),8,argument,
        {0x4D0C80,0,0,0},{0,0,0,-1});
    return word(sequence+0x14)==1 && word(sequence+0x30)==8;
}
}
