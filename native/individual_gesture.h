// Fixed body-animation arguments for the engine's single-animation sequence.
#pragma once
#include <cstdint>
namespace bwv_probe {
// Individual-body slots in the supported engine's animation table. Slots
// 200-204 are concurrent gestures and cannot substitute for these clips.
enum BodyGestureAnimation : int32_t {
    body_howl=217, body_laugh=218, body_cry=219,
    body_realization=220, body_pray=221,
};
inline bool individual_gesture_argument(int32_t animation) {
    return animation>=body_howl && animation<=body_pray;
}
inline bool individual_gesture_sequence(uint32_t count,uint32_t cursor,
        uint32_t subaction,uint32_t argument) {
    // The verified Scratch constructor creates one individual-animation entry.
    // Change its transient argument before the engine starts processing it.
    return count==1 && cursor==0 && subaction==10 && argument==60;
}
}
