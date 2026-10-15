// Original guarded experiment policy; no engine implementation or instructions.
#pragma once
#include <cstdint>
#include "throw_targets.h"
#include "movement_pace.h"

namespace bwv_probe {
// Group dancing recruits villagers and can legitimately last longer than the
// normal observation window. Keep tracking the verified active plan; this never
// extends player-command priority, reissues a command or prevents interruption.
inline bool action_monitor_timed_out(uint32_t requested_plan, bool plan_verified,
        uint32_t current_plan, uint64_t elapsed_ms, uint32_t timeout_ms) {
    if (requested_plan==309 && plan_verified && current_plan==309) return false;
    return elapsed_ms>timeout_ms;
}
enum class Phase { unavailable, ready, queued, accepted, observed, finished, rejected };
struct Command {
    static constexpr uint32_t interrupt_flag = 0x80000000u;
    static constexpr uint32_t player_command_flag = 0x40000000u;
    static constexpr uint32_t night_priority_flag = 0x08000000u;
    static constexpr uint32_t pace_mask = 0x30000000u;
    uint32_t magic = 0x42575041; // BWPA
    uint32_t version = 1;
    uint64_t epoch = 0;
    uint64_t generation = 0;
    uint64_t offered_at = 0;
    uint32_t action = 1; // 1 confused, 2 sleep, 3 hand, 4 wake, 5 object; v5 adds 6-11 named self actions.
    uint32_t reserved = 0; // v2 GoToHand: request-bound hand-target generation.
    bool interrupts() const { return (version & interrupt_flag)!=0; }
    bool player_command() const { return (version & player_command_flag)!=0; }
    bool night_priority() const { return (version & night_priority_flag)!=0; }
    uint32_t protocol() const { return version & ~(interrupt_flag|player_command_flag|night_priority_flag|pace_mask); }
    Pace pace() const {return static_cast<Pace>((version&pace_mask)>>28);}
};
static_assert(sizeof(Command) == 40);

struct Policy {
    static constexpr uint64_t cooldown_ms = 10000;
    uint64_t epoch = 0, generation = 0, offered_at = 0, accepted_at = 0;
    uint64_t finished_at = 0, attempts = 0;
    uintptr_t creature = 0;
    Phase phase = Phase::unavailable;
    bool available = false, consumed = false;
    bool repeat_enabled = false, terminal = false, verified_exit = false, catalog_enabled = false;
    uint32_t target_generation = 0;
    uint32_t object_target_generation = 0;
    uint32_t held_target_generation = 0;
    bool held_enabled = false;
    uint32_t throw_tokens[object_target_capacity]{};
    uint32_t pickup_tokens[object_target_capacity]{};
    uint32_t water_tokens[object_target_capacity]{};
    uint32_t world_tokens[world_target_capacity]{};
    uint32_t world_held_generations[world_target_capacity]{};
    bool has_water_target() const { for(auto token:water_tokens)if(token)return true;return false; }
    bool world_target(uint32_t token) const {
        if(!held_enabled || !token) return false;
        for(unsigned i=0;i<world_target_capacity;++i)
            if(world_tokens[i]==token)return world_held_generations[i]==held_target_generation;
        return false;
    }
    bool water_target(uint32_t token) const {
        if (!held_enabled || held_target_generation || !token) return false;
        for(auto value:water_tokens) if(value==token) return true;
        return false;
    }
    bool pickup_target(uint32_t token) const {
        if (!held_enabled || held_target_generation || !token) return false;
        for(auto value:pickup_tokens) if(value==token) return true;
        return false;
    }
    bool throw_target(uint32_t token) const {
        if (!held_enabled || !held_target_generation || !token) return false;
        for(auto value:throw_tokens) if(value==token) return true;
        return false;
    }
    uint32_t allowed_actions = 7;
    uint32_t queued_action = 0;
    // Cast/position slots plus the live hand target. Unrelated action-mask
    // changes must not revoke these still-valid, individually bound requests.
    uint64_t bound_floor[5]{};
    void revoke_bound() { for (auto& floor : bound_floor) floor=generation; }
    const char* reason = "waiting_for_game";

    explicit Policy(bool repeat = false, bool catalog = false, bool held = false)
        : repeat_enabled(repeat), catalog_enabled(catalog), held_enabled(held) {}

    uint64_t cooldown_remaining(uint64_t now) const {
        if (!consumed || !terminal || !verified_exit || !repeat_enabled) return 0;
        return now < finished_at ? cooldown_ms :
            (now-finished_at < cooldown_ms ? cooldown_ms-(now-finished_at) : 0);
    }

    void finish(uint64_t now, bool safely_ended, Phase result = Phase::finished) {
        if (!consumed || terminal) return;
        terminal=true; verified_exit=safely_ended; finished_at=now;
        phase=result; ++generation; revoke_bound();
    }

    void actions(uint32_t mask) {
        if (mask!=allowed_actions) {
            // A bound casting/world command must not be revoked because an
            // unrelated gesture or walking capability changed before this tick.
            // Its own capability, target, identity and scene are still checked.
            const bool bound_unchanged=phase==Phase::queued && (queued_action==3 || (queued_action>=17 && queued_action<=20))
                && (allowed_actions&(1u<<(queued_action-1))) && (mask&(1u<<(queued_action-1)));
            const auto changed=allowed_actions^mask;
            allowed_actions=mask;if(!bound_unchanged)++generation;
            for(unsigned i=0;i<4;++i) if(changed&(1u<<(16+i))) bound_floor[i]=generation;
            if(changed&4u)bound_floor[4]=generation;
        }
    }

    void observe(uint64_t session, uintptr_t identity, bool safe, const char* blocked, uint64_t now) {
        if (session != epoch || identity != creature) {
            epoch = session; creature = identity; ++generation; revoke_bound();
            available = false;
            // A changed identity cannot establish how an outstanding action ended.
            if (consumed) verified_exit=false;
            else phase=Phase::unavailable;
        }
        if (safe != available) { ++generation; revoke_bound(); available = safe; }
        if (consumed && terminal && verified_exit && repeat_enabled && safe
                && now>=finished_at && !cooldown_remaining(now)) {
            consumed=false; terminal=false; verified_exit=false; ++generation; revoke_bound();
        }
        reason = !safe ? blocked : !consumed ? "manual_test_only" :
            !terminal ? "action_in_progress" : !repeat_enabled ? "restart_required_for_another_test" :
            !verified_exit ? "uncertain_outcome_restart_required" : "action_cooldown";
        if (!consumed && phase != Phase::queued) phase = safe ? Phase::ready : Phase::unavailable;
        offered_at = now;
    }

    bool queue(const Command& command, uint64_t now) {
        const auto version=command.protocol();
        const bool priority=command.interrupts() && repeat_enabled;
        const bool catalog = version == 5 && catalog_enabled
            && (command.action==2 || (command.action>=6 && command.action<=11));
        const bool conversation = version == 2 || catalog;
        const bool wake = version == 3 && command.action == 4;
        const bool object = version == 4 && command.action == 5;
        const bool held = version == 6 && held_enabled
            && (command.action==12 || command.action==14 || command.action==21);
        const bool throwing = version==7 && held_enabled && command.action==15;
        const bool pickup = version==8 && held_enabled && command.action==16;
        const bool miracle = version==9 && held_enabled && (command.action==17 || command.action==18);
        const bool world = version==10 && held_enabled && command.action==19;
        const bool position=version==11 && held_enabled && command.action==20 && !held_target_generation;
        const bool hand=conversation && command.action==3;
        const bool generation_valid=(miracle || world || position || hand)
            ? command.generation>=bound_floor[hand?4:command.action-17] && command.generation<=generation
            : command.generation==generation;
        if (command.magic != 0x42575041 || (command.interrupts() && !priority)
                || (command.player_command() && !repeat_enabled)
                || (command.night_priority() && !command.player_command())
                || command.pace()>Pace::run || (command.pace()!=Pace::automatic && !pace_action(command.action))
                || (version != 1 && !conversation && !wake && !object && !held && !throwing && !pickup && !miracle && !world && !position)
                || (command.action < 1 || command.action > 21)
                || (command.action>=6 && !catalog && !held && !throwing && !pickup && !miracle && !world && !position)
                || (command.action == 4 && !wake)
                || (command.action == 5 && !object)
                || !(allowed_actions & (1u << (command.action-1)))
                || (allowed_actions==4 && !conversation)
                || (version==2 && command.action == 2)
                || (world ? !world_target(command.reserved)
                    : miracle && command.action==18 ? !water_target(command.reserved)
                    : pickup ? !pickup_target(command.reserved)
                    : throwing ? !throw_target(command.reserved)
                    : held ? (!held_target_generation || command.reserved != held_target_generation)
                    : object ? (!object_target_generation || command.reserved != object_target_generation)
                    : conversation && command.action == 3
                    ? (!target_generation || command.reserved != target_generation)
                    : command.reserved != 0)
                || (consumed && !priority) || !available
                || (phase != Phase::ready && !(priority && consumed && phase!=Phase::queued))
                || command.epoch != epoch || !generation_valid
                || now < command.offered_at || now-command.offered_at > (conversation ? 30000u : 1000u)
                || now < offered_at || now-offered_at > 1000
                || command.offered_at > offered_at) return false;
        if(priority) { consumed=false;terminal=false;verified_exit=false; }
        queued_action=command.action;phase = Phase::queued;
        return true;
    }

    bool dispatch(const Command& command, uint64_t now) {
        if (phase != Phase::queued) {
            if (!consumed) { phase=Phase::rejected; ++generation; reason="conditions_changed_before_dispatch"; }
            return false;
        }
        phase = Phase::ready;
        if (!queue(command, now)) {
            phase = Phase::rejected; ++generation; reason = "conditions_changed_before_dispatch";
            return false;
        }
        consumed = true; terminal=false; verified_exit=false;
        accepted_at = now; ++attempts; ++generation; revoke_bound(); phase = Phase::accepted;
        return true;
    }
};
inline const char* name(Phase phase) {
    switch (phase) {
    case Phase::ready: return "ready";
    case Phase::queued: return "queued";
    case Phase::accepted: return "accepted";
    case Phase::observed: return "native_plan_observed";
    case Phase::finished: return "finished";
    case Phase::rejected: return "rejected";
    default: return "unavailable";
    }
}
}
