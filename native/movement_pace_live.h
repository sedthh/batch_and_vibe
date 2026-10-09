// Original scoped adapter for the engine's normalized movement pace API.
#pragma once
namespace bwv_probe {
inline bool read_pace(uintptr_t creature,PaceReading& value) {
    if(!type(creature,".?AVCreature@@"))return false;
    const auto physical=word(creature+0x160),info=word(creature+0x28);
    value.body=word(physical+0x58);
    // Native RunToObject and the normal creature update use these two presets.
    // Resolve the controller afresh rather than retaining a body across loads.
    return type(value.body,".?AVLH3DCreature@@")
        && word(0x9D1698+122*80)==0x4A6EA0
        && range(0x47FA20,0x57)
        && read(info+0x254,value.walk) && read(info+0x258,value.run)
        && read(value.body+0x483C,value.current) && valid_paces(value.walk,value.run)
        && std::isfinite(value.current) && value.current>=0 && value.current<=1;
}
inline void set_pace(uintptr_t body,float speed) {
    using SetSpeed=void(__thiscall*)(void*,float);
    reinterpret_cast<SetSpeed>(0x47FA20)(reinterpret_cast<void*>(body),speed);
}
PaceLease movement_pace;
bool movement_pace_verified=false;
PaceReading current_pace;
Pace requested_pace=Pace::automatic;
}
