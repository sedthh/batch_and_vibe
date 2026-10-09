// Verified in-map hand destinations; distance is not a nearby-only test limit.
#pragma once
#include <cmath>
#include <cstdint>

namespace bwv_probe {
struct Movement {
    bool verified = false;
    unsigned interfaces = 0;
    const char* validation = "not_sampled";
    double creature_x = 0, creature_z = 0, hand_x = 0, hand_z = 0;
    double distance = 0;
    double width = 0, depth = 0;
};
inline const char* movement_block(const Movement& value) {
    if (!value.verified || !std::isfinite(value.distance) || value.distance < 0)
        return "hand_position_unverified";
    if (value.distance == 0) return "already_at_hand_position";
    return nullptr;
}
inline bool world_point(double x, double z, double width, double depth) {
    return std::isfinite(x) && std::isfinite(z) && std::isfinite(width)
        && std::isfinite(depth) && width > 0 && depth > 0
        && x > 0 && z > 0 && x < width && z < depth;
}
// GoToHand follows the live hand, not a frozen ground position. Movement alone
// must not revoke that target while the model thinks or the creature wakes.
// Unavailable hand data still revokes it; session/creature identity is separate.
struct MovementTarget {
    uint32_t generation = 0;
    bool valid = false, exhausted = false;
    double x = 0, z = 0;
    void observe(const Movement& value) {
        const bool next = !movement_block(value) && std::isfinite(value.hand_x)
            && std::isfinite(value.hand_z);
        if (next != valid) {
            if (generation == UINT32_MAX) exhausted = true;
            else ++generation;
            x=value.hand_x; z=value.hand_z;
        }
        valid=next;
        if (next) { x=value.hand_x; z=value.hand_z; }
    }
    uint32_t token() const { return valid && !exhausted ? generation : 0; }
};
inline uint32_t hand_approach_mask(const Movement& movement, bool activity_verified, bool holding) {
    // Like coordinate and object movement, SetAgenda exits sleep natively.
    return !holding && activity_verified && !movement_block(movement) ? 4u : 0u;
}
}
