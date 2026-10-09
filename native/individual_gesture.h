// Fixed body-animation arguments for the engine's single-animation sequence.
#pragma once
#include <cstdint>
namespace bwv_probe {
inline bool individual_gesture_argument(int32_t animation) {
    return animation>=200 && animation<=204;
}
inline bool individual_gesture_sequence(uint32_t count,uint32_t cursor,
        uint32_t subaction,uint32_t argument) {
    // The verified Scratch constructor creates one individual-animation entry.
    // Change its transient argument before the engine starts processing it.
    return count==1 && cursor==0 && subaction==10 && argument==60;
}
}
