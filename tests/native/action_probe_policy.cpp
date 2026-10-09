// Original synthetic policy tests. No game imports, addresses or assets.
#include "../../native/action_probe_policy.h"
#include "check.h"
#include <cstdio>
#include <initializer_list>
using namespace bwv_probe;
Command offer(const Policy& p) {
    Command c; c.epoch=p.epoch; c.generation=p.generation; c.offered_at=p.offered_at;
    return c;
}
int main() {
    // A verified group dance can outlive one minute without a false timeout.
    assert(!action_monitor_timed_out(309,true,309,60001,60000));
    assert(!action_monitor_timed_out(309,true,309,3600000,60000));
    // Lost plan visibility remains bounded; other recipes keep their limits.
    assert(action_monitor_timed_out(309,false,309,60001,60000));
    assert(action_monitor_timed_out(309,true,0,60001,60000));
    assert(action_monitor_timed_out(89,true,89,45001,45000));
    assert(!action_monitor_timed_out(309,false,0,60000,60000));
    // A moving creature can lose a nearby-tree offer while the hand remains
    // valid. That unrelated capability change must not reject GoToHand.
    for(unsigned flags:{0u,Command::interrupt_flag|Command::player_command_flag}) {
        Policy p(true,true,true);p.actions(4u|16u);p.observe(1,42,true,"",100);
        p.target_generation=7;
        auto c=offer(p);c.action=3;c.version=2|flags;c.reserved=7;
        p.actions(4u);p.observe(1,42,true,"",110);
        assert(p.queue(c,120));assert(p.dispatch(c,130));
        assert(!p.queue(c,140)); // Receipt consumed: never replay the old packet.
    }
    for(unsigned change=0;change<5;++change) {
        Policy p(true,true,true);p.actions(4u);p.observe(1,42,true,"",100);
        p.target_generation=7;
        auto c=offer(p);c.action=3;c.version=2|Command::interrupt_flag;c.reserved=7;
        if(change==0){p.actions(0);p.actions(4u);} // Hand capability was revoked.
        if(change==1){p.observe(1,42,false,"menu",110);p.observe(1,42,true,"",120);}
        if(change==2)p.observe(2,42,true,"",120);
        if(change==3)p.observe(1,43,true,"",120);
        if(change==4)p.target_generation=8;
        assert(!p.queue(c,130));
    }
    { // Position packets have their own version and current-session capability.
        Policy p(true,true,true);p.actions(1u<<19);p.observe(1,42,true,"",100);
        auto c=offer(p);c.action=20;c.version=11;
        auto wrong=c;wrong.version=10;assert(!p.queue(wrong,100));
        wrong=c;wrong.epoch=2;assert(!p.queue(wrong,100));
        assert(p.queue(c,100));assert(p.dispatch(c,100));assert(!p.queue(c,100));
        p.observe(2,42,true,"",101);assert(!p.queue(c,101));
    }
    // Explicit priority may replace an active or uncertain command, and skip
    // cooldown. It never admits a stale packet, a duplicate or a scene lock.
    for(int terminal=0;terminal<3;++terminal) {
        Policy p(true,true,true);p.actions(1u<<18);p.world_tokens[0]=55;
        p.observe(1,42,true,"",100);
        auto c=offer(p);c.version=10;c.action=19;c.reserved=55;
        assert(p.queue(c,100));assert(p.dispatch(c,100));
        if(terminal)p.finish(110,terminal==1);
        p.observe(1,42,true,"",120);
        auto next=offer(p);next.version=10;next.action=19;next.reserved=55;
        assert(!p.queue(next,120));
        next.version|=Command::interrupt_flag;
        assert(p.queue(next,120));assert(p.dispatch(next,120));
        assert(p.attempts==2);assert(!p.queue(next,120));
        auto locked=offer(p);locked.version=10|Command::interrupt_flag;locked.action=19;locked.reserved=55;
        p.observe(1,42,false,"cutscene",121);assert(!p.queue(locked,121));
        p.observe(1,42,true,"",122);assert(!p.queue(locked,122));
        auto invalid=offer(p);invalid.version=10|Command::interrupt_flag;invalid.action=19;invalid.reserved=99;
        assert(!p.queue(invalid,122));
    }
    // Changes to other capabilities must not invalidate a bound cast packet.
    for (unsigned action=17;action<=19;++action) {
        Policy bound(true,true,true);
        bound.actions(1u<<(action-1));bound.observe(1,42,true,"",100);
        bound.water_tokens[0]=55;bound.world_tokens[0]=55;
        auto c=offer(bound);c.action=action;c.version=action==19?10:9;c.reserved=action==17?0:55;
        bound.actions(bound.allowed_actions|8u);bound.observe(1,42,true,"",110);
        assert(bound.queue(c,120));assert(bound.dispatch(c,130));
    }
    for (unsigned revoke=0;revoke<3;++revoke) {
        Policy bound(true,true,true);
        bound.actions(1u<<18);bound.observe(1,42,true,"",100);bound.world_tokens[0]=55;
        auto c=offer(bound);c.action=19;c.version=10;c.reserved=55;
        if(revoke==0) {bound.actions(0);bound.actions(1u<<18);}
        if(revoke==1) {bound.observe(1,42,false,"menu",110);bound.observe(1,42,true,"",120);}
        if(revoke==2) {bound.observe(1,43,true,"",110);bound.observe(1,42,true,"",120);}
        assert(!bound.queue(c,130));
    }
    Policy objects(true);
    objects.actions(17);objects.observe(1,42,true,"",100);objects.object_target_generation=7;
    auto object=offer(objects);object.action=5;object.reserved=7;
    assert(!objects.queue(object,110));
    object.version=2;assert(!objects.queue(object,110)); // Never a model/tool offer.
    object.version=4;object.reserved=0;assert(!objects.queue(object,110));
    object.reserved=7;assert(objects.queue(object,110));
    objects.object_target_generation=8;assert(!objects.dispatch(object,120));
    objects.observe(1,42,true,"",130);object=offer(objects);object.action=5;object.version=4;object.reserved=8;
    assert(objects.queue(object,140));assert(objects.dispatch(object,150));
    assert(!objects.queue(object,160));
    Policy ordinary;ordinary.observe(1,42,true,"",100);
    object=offer(ordinary);object.action=5;object.version=4;object.reserved=7;
    assert(!ordinary.queue(object,110)); // Ordinary builds have no object capability.
    Policy expired;expired.actions(16);expired.observe(1,42,true,"",100);expired.object_target_generation=7;
    object=offer(expired);object.action=5;object.version=4;object.reserved=7;
    assert(!expired.queue(object,1101));
    Policy repeat(true);
    repeat.observe(1,42,true,"",100);
    auto repeat_command=offer(repeat);
    assert(repeat.queue(repeat_command,110));
    assert(repeat.dispatch(repeat_command,120));
    assert(repeat.attempts==1);
    repeat.observe(1,42,true,"",50000);
    assert(!repeat.queue(offer(repeat),50000)); // Time alone never clears active work.
    repeat.finish(50000,true);
    assert(repeat.cooldown_remaining(50000)==10000);
    repeat.observe(1,42,true,"",59999);
    assert(!repeat.queue(offer(repeat),59999));
    repeat.observe(1,42,false,"cutscene",60000);
    assert(!repeat.queue(offer(repeat),60000));
    repeat.observe(1,42,true,"",60001);
    assert(repeat.phase==Phase::ready && !repeat.consumed);
    // A previously delivered packet never revives, even with a forged fresh time.
    repeat_command.offered_at=60001;
    assert(!repeat.queue(repeat_command,60001));
    repeat_command=offer(repeat);
    assert(repeat.queue(repeat_command,60002));
    assert(!repeat.queue(repeat_command,60003));
    assert(repeat.dispatch(repeat_command,60004));
    assert(repeat.attempts==2);
    repeat.finish(60005,false); // Timeout/uncertain outcome never rearms.
    repeat.finish(60006,true); // Later observations cannot rewrite the frozen result.
    repeat.observe(1,42,true,"",100000);
    assert(!repeat.queue(offer(repeat),100000));
    Policy switched(true);
    switched.observe(1,42,true,"",100);
    auto switch_command=offer(switched);
    assert(switched.queue(switch_command,110));assert(switched.dispatch(switch_command,120));
    switched.finish(200,true);
    switched.observe(2,42,true,"",11000);
    assert(!switched.queue(offer(switched),11000)); // No rearm across a loaded save.
    Policy legacy;
    legacy.observe(1,42,true,"",100);
    auto legacy_command=offer(legacy);
    assert(legacy.queue(legacy_command,110));assert(legacy.dispatch(legacy_command,120));
    legacy.finish(200,true);legacy.observe(1,42,true,"",11000);
    assert(!legacy.queue(offer(legacy),11000)); // Default experiment stays one-shot.
    Policy social;
    social.actions(4); social.observe(1,42,true,"",100); social.target_generation=2;
    auto social_command=offer(social);
    assert(!social.queue(social_command,110)); // Cosmetic gesture cannot interrupt.
    social_command.action=3;
    assert(!social.queue(social_command,110)); // Legacy/manual lane cannot interrupt.
    social_command.version=2; social_command.reserved=2;
    assert(social.queue(social_command,110));
    social.actions(7); social.observe(1,42,true,"",120);
    assert(social.dispatch(social_command,120)); // Unrelated activity changes preserve a bound hand command.
    Policy waking;
    waking.actions(8); waking.observe(1,42,true,"",100);
    auto wake_command=offer(waking); wake_command.action=4;
    assert(!waking.queue(wake_command,110));
    wake_command.version=2; assert(!waking.queue(wake_command,110));
    wake_command.version=3; assert(waking.queue(wake_command,110));
    waking.actions(0); waking.observe(1,42,false,"script",120);
    assert(!waking.dispatch(wake_command,120));
    waking.actions(8); waking.observe(1,42,true,"",130);
    assert(!waking.queue(wake_command,140));
    wake_command=offer(waking);wake_command.version=3;wake_command.action=4;
    assert(waking.queue(wake_command,140)); assert(waking.dispatch(wake_command,150));
    waking.actions(7); waking.observe(1,42,true,"",160);
    assert(!waking.queue(offer(waking),170)); // Waking consumes the same process budget.
    Policy p;
    p.observe(1,42,true,"",100);
    const auto first=offer(p);
    assert(p.queue(first,120));
    assert(!p.queue(first,130)); // Duplicate cannot replace a queued command.
    p.observe(1,42,false,"cutscene",150);
    assert(!p.dispatch(first,160));
    assert(!p.consumed);
    p.observe(1,42,true,"",170);
    assert(!p.queue(first,180)); // Released control needs a new generation.
    auto current=offer(p);
    assert(!p.queue(current,1200)); // Expired offer.
    auto altered=current; altered.action=4;
    assert(!p.queue(altered,180));
    altered=current; altered.reserved=1;
    assert(!p.queue(altered,180));
    altered=current; altered.magic=0;
    assert(!p.queue(altered,180));
    altered=current; altered.version=3;
    assert(!p.queue(altered,180));
    altered=current; altered.offered_at=200;
    assert(!p.queue(altered,180)); // Future timestamps cannot grant permission.
    assert(p.queue(current,180));
    p.observe(1,99,true,"",190);
    assert(!p.dispatch(current,200)); // Different creature cancels the queued action.
    assert(p.phase==Phase::rejected);
    p.observe(1,99,true,"",200);
    current=offer(p);
    assert(p.queue(current,200));
    p.observe(2,99,true,"",210);
    assert(!p.dispatch(current,220)); // Loading another save cancels too.
    assert(p.phase==Phase::rejected);
    p.observe(2,99,true,"",220);
    current=offer(p);
    assert(p.queue(current,220));
    assert(p.dispatch(current,230));
    assert(p.consumed && p.phase==Phase::accepted);
    assert(!p.queue(current,240));
    p.observe(3,101,true,"",250);
    assert(!p.queue(offer(p),260)); // One accepted action per process, not per save.
    Policy sleep;
    sleep.observe(1,42,true,"",100);
    auto command=offer(sleep); command.action=2;
    assert(sleep.queue(command,110));
    assert(sleep.dispatch(command,120));
    assert(!sleep.queue(command,130)); // Sleep shares the same one-attempt budget.
    Policy movement;
    movement.observe(1,42,true,"",100);
    command=offer(movement); command.action=3;
    assert(movement.queue(command,110));
    movement.observe(1,42,false,"sleep",120);
    assert(!movement.dispatch(command,130));
    assert(!movement.consumed);
    movement.observe(1,42,true,"",140);
    command=offer(movement); command.action=3;
    assert(movement.queue(command,150));
    assert(movement.dispatch(command,160));
    assert(!movement.queue(command,170));
    Policy delayed;
    delayed.observe(1,42,true,"",100);
    command=offer(delayed); command.version=2;
    delayed.observe(1,42,true,"",5000);
    assert(delayed.queue(command,5010));
    assert(delayed.dispatch(command,5020)); // Original offer, not refreshed.
    Policy stale;
    stale.observe(1,42,true,"",100);
    command=offer(stale); command.version=2;
    stale.observe(1,42,true,"",30200);
    assert(!stale.queue(command,30201));
    Policy target;
    target.observe(1,42,true,"",100); target.target_generation=1;
    command=offer(target); command.version=2; command.action=3; command.reserved=1;
    target.observe(1,42,true,"",5000); target.target_generation=3;
    assert(!target.queue(command,5010)); // Moved away and back.
    command.reserved=3;
    assert(target.queue(command,5010));
    target.target_generation=4;
    assert(!target.dispatch(command,5020)); // Changed after queue receipt.
    target.observe(1,42,true,"",5030);
    command=offer(target); command.version=2; command.action=2;
    assert(!target.queue(command,5040)); // Sleep is manual QA only.
    Policy sleeping;
    sleeping.observe(1,42,true,"",100);
    command=offer(sleeping); command.version=2;
    sleeping.observe(1,42,false,"sleep",500);
    sleeping.observe(1,42,true,"",5000);
    assert(!sleeping.queue(command,5010)); // Waking never revives old permission.
    std::puts("Action probe policy passed: expiry, duplicate, locks, identity, session, cooldown, uncertain outcomes");
}
