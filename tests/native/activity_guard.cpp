#include "../../native/activity_guard.h"
#include "../../native/action_probe_policy.h"
#include "check.h"
#include <cstdio>
#include <initializer_list>
using namespace bwv_probe;

int main() {
    assert(activity_block(Activity{}) != nullptr);
    Activity awake{true,0,0,false};
    assert(awake_activity(awake));
    Activity walking{true,1,0,false};walking.current_subaction=61;
    assert(awake_activity(walking));
    walking.sleep_in_sequence=true;assert(!awake_activity(walking));
    walking.sleep_in_sequence=false;walking.current_subaction=132;assert(!awake_activity(walking));
    assert(activity_block(awake) == nullptr);
    for (uint32_t mode=1; mode<=37; ++mode) {
        assert(activity_block(Activity{true,mode,29,false}) != nullptr);
        assert(activity_block(Activity{true,mode,40,false}) != nullptr);
    }
    assert(activity_block(Activity{true,38,0,false}) != nullptr);
    assert(activity_block(Activity{true,UINT32_MAX,0,false}) != nullptr);
    assert(activity_block(Activity{true,0,29,false}) != nullptr);
    assert(activity_block(Activity{true,0,0,true}) != nullptr);
    assert(activity_actions(awake,0)==7);
    assert(activity_actions(awake,161)==7);
    assert(activity_actions(awake,161,true,9)==7);
    assert(activity_actions(Activity{true,1,0,false},161,true,9)==5);
    assert(activity_actions(awake,295)==4);
    assert(activity_actions(Activity{true,1,0,false},295)==4);
    assert(activity_actions(Activity{true,10,67,false},295)==0);
    assert(activity_actions(Activity{true,0,0,true},295)==0);
    assert(activity_actions(awake,101)==0); // Never interrupt a miracle.
    assert(activity_actions(awake,28)==0); // No generic busy-plan exception.
    // Repeat QA replaces routine plans at standing/walking boundaries instead
    // of waiting for the entire high-level AI plan to become idle.
    for (uint32_t plan : {1u,3u,8u,20u,80u,82u,172u,188u,189u,190u,191u,192u,193u,194u,195u,284u}) {
        assert(activity_actions(awake,plan)==0); // Default experiment unchanged.
        assert(activity_actions(awake,plan,true,3)==5);
        assert(activity_actions(Activity{true,1,0,false},plan,true,10)==5);
        for (uint32_t desire : {24u,26u,33u,40u,UINT32_MAX})
            assert(activity_actions(awake,plan,true,desire)==0);
        assert(activity_actions(Activity{true,0,0,true},plan,true,3)==0);
        assert(activity_actions(Activity{true,3,29,false},plan,true,3)==0);
        assert(activity_actions(Activity{true,10,67,false},plan,true,3)==0);
        assert(activity_actions(Activity{},plan,true,3)==0);
    }
    for (uint32_t plan : {19u,28u,101u,159u,203u,313u,322u,999u}) {
        assert(activity_actions(awake,plan,true,3)==0);
        assert(activity_actions(Activity{true,1,0,false},plan,true,3)==0);
    }
    // A casting/feeding goal is interruptible while travelling to its target;
    // the same goal must lose that permission once its interaction starts.
    Activity approaching{true,1,0,false}; approaching.current_subaction=61;
    for (uint32_t plan : {28u,48u,55u,101u,155u,233u,291u}) {
        assert(activity_actions(approaching,plan)==0);
        assert(activity_actions(approaching,plan,true,1)==5);
        for (uint32_t desire : {24u,26u,33u,40u,UINT32_MAX})
            assert(activity_actions(approaching,plan,true,desire)==0);
        Activity standing=approaching;standing.controller_mode=0;
        assert(activity_actions(standing,plan,true,1)==5);
        assert(activity_actions(standing,plan)==0);
        for (uint32_t mode : {2u,3u,4u,10u,22u,38u}) {
            Activity changed=approaching;changed.controller_mode=mode;
            assert(activity_actions(changed,plan,true,1)==0);
        }
        Activity changed=approaching;changed.current_subaction=62;
        assert(activity_actions(changed,plan,true,1)==0);
        changed=approaching;changed.animation=29;
        assert(activity_actions(changed,plan,true,1)==0);
        changed=approaching;changed.sleep_in_sequence=true;
        assert(activity_actions(changed,plan,true,1)==0);
        changed=approaching;changed.verified=false;
        assert(activity_actions(changed,plan,true,1)==0);
        Policy travel(true);
        travel.actions(activity_actions(approaching,plan,true,1));
        travel.observe(1,42,true,"",100);
        Command command;command.epoch=1;command.generation=travel.generation;command.offered_at=100;
        assert(travel.queue(command,110));
        changed=approaching;changed.controller_mode=10;changed.current_subaction=62;
        travel.actions(activity_actions(changed,plan,true,1));
        travel.observe(1,42,false,"interaction_started",120);
        assert(!travel.dispatch(command,120));
        assert(!travel.consumed);
    }
    for (uint32_t plan : {328u,999u,UINT32_MAX})
        assert(activity_actions(approaching,plan,true,1)==0);
    // Target approach applies across valid goals, including ones never seen in
    // a capture. It still requires a researched movement subaction and mode.
    for (uint32_t subaction : {0u,61u}) {
        approaching.current_subaction=subaction;
        for (uint32_t mode : {0u,1u}) {
            approaching.controller_mode=mode;
            for (uint32_t plan=0;plan<328;++plan)
                assert(travelling_can_yield(approaching,plan,3));
            for (uint32_t desire : {24u,26u,33u,40u,UINT32_MAX})
                assert(!travelling_can_yield(approaching,42,desire));
        }
        approaching.sleep_in_sequence=true;
        assert(!travelling_can_yield(approaching,313,8));
        approaching.sleep_in_sequence=false;
    }
    Activity waiting{true,0,0,false};waiting.current_subaction=0;
    assert(activity_actions(waiting,42,true,3)==5);
    Policy wait_policy(true);
    wait_policy.actions(21);wait_policy.object_target_generation=7;
    wait_policy.observe(1,42,true,"",100);
    Command wait_command;wait_command.version=4;wait_command.action=5;wait_command.reserved=7;
    wait_command.epoch=1;wait_command.generation=wait_policy.generation;wait_command.offered_at=100;
    assert(wait_policy.queue(wait_command,110));
    waiting.controller_mode=10;waiting.animation=67;waiting.current_subaction=62;
    assert(activity_actions(waiting,42,true,3)==0);
    wait_policy.actions(0);wait_policy.observe(1,42,false,"interaction_started",120);
    assert(!wait_policy.dispatch(wait_command,120));
    Policy routine(true);
    routine.actions(5);routine.observe(1,42,true,"",100);
    Command routine_command;routine_command.epoch=1;routine_command.generation=routine.generation;
    routine_command.offered_at=100;routine_command.action=2;
    assert(!routine.queue(routine_command,110)); // Routine interruption cannot force sleep.
    routine_command.action=1;assert(routine.queue(routine_command,110));
    routine.actions(0);routine.observe(1,42,false,"miracle_started",120);
    assert(!routine.dispatch(routine_command,120)); // Fresh game-thread protection.
    Activity asleep{true,3,29,true}; asleep.current_subaction=132;
    assert(activity_actions(asleep,0)==8);
    assert(activity_actions(asleep,313)==8);
    assert(activity_actions(asleep,203)==0); // Sleeping with another creature.
    asleep.controller_mode=2; assert(activity_actions(asleep,17)==8);
    asleep.controller_mode=4; assert(activity_actions(asleep,17)==0);
    asleep.controller_mode=3; asleep.current_subaction=1;
    assert(activity_actions(asleep,17)==0); // Queued sleep is not a wake target.
    // A command offered while awake must be revoked if sleep starts before the
    // game-thread dispatch, even when the higher-level plan remains Undefined.
    for (const Activity activity : {Activity{true,2,29,false},Activity{true,3,29,false},
            Activity{true,4,29,false},Activity{true,0,0,true},Activity{}}) {
        Policy policy;
        policy.observe(1,42,true,"",100);
        Command command; command.epoch=1; command.generation=policy.generation; command.offered_at=100;
        assert(policy.queue(command,110));
        policy.observe(1,42,activity_block(activity)==nullptr,"activity_blocked",120);
        assert(!policy.dispatch(command,120));
        assert(!policy.consumed);
        policy.observe(1,42,true,"",130);
        assert(!policy.queue(command,130)); // Waking cannot revive an old offer.
    }
    std::puts("Activity guard passed: sleep entry/loop/exit, queued sleep, unknown state, dispatch revocation");
}
