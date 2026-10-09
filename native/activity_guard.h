// Original conservative policy for the exact-build animation observations.
#pragma once
#include <cstdint>

namespace bwv_probe {
struct Activity {
    bool verified = false;
    uint32_t controller_mode = UINT32_MAX;
    uint32_t animation = UINT32_MAX;
    bool sleep_in_sequence = false;
    const char* validation = "controller_unverified";
    uint32_t current_subaction = UINT32_MAX;
};

inline bool awake_activity(const Activity& activity) {
    return activity.verified && !activity.sleep_in_sequence && activity.animation==0
        && activity.controller_mode<=1 && activity.current_subaction!=132;
}

inline const char* activity_block(const Activity& activity) {
    if (!activity.verified || activity.controller_mode > 37) return "activity_unverified";
    if (activity.sleep_in_sequence) return "sleep_sequence_pending_or_active";
    // The native Sleep subaction requests animation 29 through the generic
    // enter/loop/exit controller. Do not interrupt any of those phases.
    if (activity.animation == 29 && activity.controller_mode >= 2 && activity.controller_mode <= 4)
        return "sleep_animation_or_transition";
    // The native busy query tests this mode for nonzero. Blocking every busy
    // mode also avoids overriding an unrelated gesture, feeding or wake-up.
    if (activity.controller_mode != 0) return "native_animation_busy";
    if (activity.animation != 0) return "animation_reset_incomplete";
    return nullptr;
}

// Exact-build routine plan labels, independently checked against runtime metadata.
inline const char* routine_plan_name(uint32_t plan) {
    switch (plan) {
    case 0: return "Undefined";
    case 1: return "MoveToPos";
    case 3: return "LookingAtObjectReaction";
    case 8: return "LookAtHand";
    case 20: return "ExamineByLooking";
    case 80: return "LookAtFlyingObject";
    case 82: return "LookAtCamera";
    case 161: return "BeIdle";
    case 172: return "BeSillyWithPlayer";
    case 188: return "LookButDontApproach";
    case 189: return "LookForever";
    case 190: return "LookAtCitadel";
    case 191: return "LookAtMountains";
    case 192: return "LookOutToSea";
    case 193: return "LookAtSun";
    case 194: return "LookAtMoon";
    case 195: return "LookDownCliff";
    case 284: return "WatchPlayer";
    default: return nullptr;
    }
}

inline bool routine_can_yield(const Activity& activity, uint32_t plan, uint32_t desire) {
    // Obey-player/creature and follow-player goals retain priority. Unknown
    // desire metadata cannot grant permission to interrupt a routine plan.
    return desire<40 && desire!=24 && desire!=26 && desire!=33
        && routine_plan_name(plan) && activity.verified && !activity.sleep_in_sequence
        && activity.animation==0 && activity.controller_mode<=1;
}

inline bool travelling_can_yield(const Activity& activity, uint32_t plan, uint32_t desire) {
    // Subactions 0 and 61 approach a target and have null end callbacks;
    // SetAgenda's shared cleanup cancels their walking path. Controller mode 0
    // also occurs while that subaction is standing/waiting for a path; requiring
    // mode 1 alone can leave a stalled approach permanently uninterruptible.
    // Both modes have no active animation. The high-level
    // goal need not be idle or appear on a growing list of goal names.
    // Recheck the subaction on the engine thread: an approach becoming a cast
    // must revoke the offer even when the high-level plan has not changed.
    return desire<40 && desire!=24 && desire!=26 && desire!=33
        && plan<328 && activity.verified && !activity.sleep_in_sequence
        && (activity.current_subaction==0 || activity.current_subaction==61)
        && activity.controller_mode<=1
        && activity.animation==0;
}

// Opt-in QA lanes. Hard scene/ownership/body checks run independently first.
// A direct question may replace this one researched social plan at a standing
// or walking boundary. Animation/paired interaction modes remain excluded.
inline uint32_t activity_actions(const Activity& activity, uint32_t plan,
                                bool routine_interruptions=false, uint32_t desire=UINT32_MAX) {
    if (!activity.verified) return 0;
    if (activity.current_subaction==132 && activity.animation==29
            && (activity.controller_mode==2 || activity.controller_mode==3)
            && (plan==0 || plan==17 || plan==78 || plan==85 || plan==313)) return 8;
    if (activity.sleep_in_sequence) return 0;
    if (plan==295 && (activity.controller_mode==0 || activity.controller_mode==1)
            && activity.animation==0) return 4;
    if (!activity_block(activity) && (plan==0 || plan==161)) return 7;
    if (routine_interruptions && (routine_can_yield(activity,plan,desire)
            || travelling_can_yield(activity,plan,desire))) return 5;
    return 0;
}
}
