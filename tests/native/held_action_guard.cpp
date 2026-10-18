#include "check.h"
#include "../../native/held_action_guard.h"
#include "../../native/action_probe_policy.h"
#include <cstring>
#include <map>
using namespace bwv_probe;
int main() {
    HeldTarget t;t.observe(1,42,100,200,1);auto first=t.token();assert(first);
    t.observe(1,42,100,200,1);assert(t.token()==first);
    t.observe(1,42,100,200,1,true);assert(t.token()==first && t.can_eat);
    t.observe(1,42,100,200,1,false);assert(t.token()==first && !t.can_eat);
    // Loss of edibility disables eating, but does not change the held item or
    // cancel inspecting, carrying or putting that same item down.
    for(uint32_t action:{12u,14u,23u,26u}) {
        Policy p(true,true,true);p.actions(held_action_mask(1,true));
        p.held_target_generation=first;p.observe(1,42,true,"",100);
        Command c;c.version=6;c.action=action;c.reserved=first;
        c.epoch=1;c.generation=p.generation;c.offered_at=100;
        assert(p.queue(c,110));p.actions(held_action_mask(1,false));
        assert(p.dispatch(c,120)==(action!=14));
    }
    t.observe(1,42,0,0,0);assert(!t.token());
    t.observe(1,42,100,200,1);assert(t.token()!=first); // Released and reacquired.
    auto old=t.token();t.observe(2,42,100,200,1);assert(t.token()!=old);
    old=t.token();t.observe(2,43,100,200,1);assert(t.token()!=old);
    assert((held_action_mask(1,true)&(1u<<13)) && !(held_action_mask(2)&(1u<<13)));
    assert(!held_action_mask(0) && held_action_mask(4)==((1u<<11)|(1u<<20)|(31u<<22)));
    for (uint32_t kind=2;kind<=10;++kind) assert(held_action_mask(kind)==((1u<<11)|(1u<<20)|(31u<<22)));
    for(uint32_t kind=1;kind<=10;++kind) {
        assert(held_action_mask(kind,true)&(1u<<13));
        assert(!(held_action_mask(kind,false)&(1u<<13)));
    }
    old=t.token();t.observe(2,43,100,200,1,true);assert(t.can_eat && t.token()==old);
    old=t.token();t.observe(2,43,100,200,1,false);assert(!t.can_eat && t.token()==old);
    assert(!held_recipe(13)); // Retired examination command.
    assert(held_recipe(23)->plan==275 && held_recipe(23)->constructor==0x4BE460);
    assert(held_recipe(21)->plan==67 && held_recipe(21)->constructor==0x4A3720);
    int selected=-1;
    const auto apply=[&](int value){selected=value;};
    assert(configure_held_discard(1,0,1,97,apply) && selected==95);
    selected=-1;
    assert(!configure_held_discard(2,0,1,97,apply));
    assert(!configure_held_discard(1,1,1,97,apply));
    assert(!configure_held_discard(1,0,5,97,apply)); // aimed throw is not discard
    assert(!configure_held_discard(1,0,1,96,apply));
    assert(selected==-1);
    HeldExaminationVariation examination;
    // One held-only animation per request; no added pickup or release entries.
    // Explicit commands choose exactly their slot. Invalid sequences never write.
    const int slots[]={103,101,102,100};
    for(uint32_t action=23;action<=27;++action) {
        selected=-1;
        assert(!examination.configure(action,2,0,3,103,apply));
        assert(!examination.configure(action,1,1,3,103,apply));
        assert(!examination.configure(action,1,0,1,103,apply));
        assert(!examination.configure(action,1,0,3,97,apply));
        assert(selected==-1);
        if(action>23) {
            int writes=0;
            assert(examination.configure(action,1,0,3,103,[&](int value){++writes;selected=value;}));
            assert(writes==1 && selected==slots[action-24]);
        }
    }
    unsigned seen=0;
    for(int i=0;i<100;++i) {
        int writes=0;
        assert(examination.configure(23,1,0,3,103,[&](int value){++writes;selected=value;}));
        assert(writes==1 && selected>=101 && selected<=103); // Never stroke (100).
        seen|=1u<<(selected-101);
    }
    assert(seen==7); // All three examination variants remain reachable.
    selected=-1;
    assert(!examination.configure(22,1,0,3,103,apply));
    assert(!examination.configure(28,1,0,3,103,apply));
    assert(selected==-1);
    assert(held_recipe(22)->plan==84);
    assert(held_home_recipe(8,false).plan==322); // toy
    assert(held_home_recipe(8,true).plan==322); // toy placement takes precedence
    for(uint32_t kind=1;kind<=10;++kind) if(kind!=8) {
        assert(held_home_recipe(kind,true).plan==164); // engine-edible, not just food piles
        assert(held_home_recipe(kind,false).plan==84);
    }
    std::map<uintptr_t,uint32_t> home{{0x11FC,0},{0x1200,65536},{0x1204,131072},
        {0x1208,0},{0x59C8,512},{0x59C4,512}};
    const auto read=[&](uintptr_t address,auto& value) {
        auto found=home.find(address);if(found==home.end())return false;
        std::memcpy(&value,&found->second,4);return true;
    };
    assert(held_home_available(0,0,read));
    assert(!held_home_blocker(0,0,read));
    // A cleared autonomous home flag must not hide a valid delivery destination.
    home[0x11FC]=1;assert(held_home_available(0,0,read));
    home.erase(0x11FC);assert(held_home_available(0,0,read));
    home[0x1208]=0x7FC00000;assert(!std::strcmp(held_home_blocker(0,0,read),"home_position_invalid"));home[0x1208]=0;
    home[0x1200]=0;assert(!held_home_available(0,0,read));home[0x1200]=65536;
    home[0x1204]=0;assert(!held_home_available(0,0,read));home[0x1204]=131072;
    home[0x1200]=0xFFFFFFFF;assert(!held_home_available(0,0,read));home[0x1200]=65536;
    home[0x1208]=0x7FC00000;assert(!held_home_available(0,0,read));home[0x1208]=0;
    home[0x59C8]=0;assert(!held_home_available(0,0,read));home[0x59C8]=512;
    home.erase(0x1204);assert(!held_home_available(0,0,read));
    assert(!std::strcmp(held_home_blocker(0,0,read),"home_read_unavailable"));
    t.observe(2,43,100,200,4,false,false,"home_position_invalid");
    assert(!std::strcmp(t.home_blocker,"home_position_invalid") && !t.can_bring_home);
    t.observe(2,43,100,200,4,false,true);assert(!t.home_blocker && t.can_bring_home);
    t.observe(2,43,0,0,0);assert(!std::strcmp(t.home_blocker,"empty_hand") && !t.can_bring_home);
    for(uint32_t kind=1;kind<=10;++kind) {
        const auto mask=held_action_mask(kind,false,true);
        assert(mask&(1u<<21));assert(!(held_action_mask(kind)&(1u<<21)));
        Policy home_policy(true,true,true);home_policy.actions(mask);home_policy.held_target_generation=7;
        home_policy.observe(1,42,true,"",100);
        Command home_command;home_command.version=6;home_command.action=22;home_command.reserved=7;
        home_command.epoch=1;home_command.generation=home_policy.generation;home_command.offered_at=100;
        auto stale=home_command;stale.reserved=8;assert(!home_policy.queue(stale,105));
        assert(home_policy.queue(home_command,110));
        home_policy.actions(held_action_mask(kind)); // home unavailable at dispatch
        assert(!home_policy.dispatch(home_command,120));
        Policy fresh(true,true,true);fresh.actions(mask);fresh.held_target_generation=7;
        fresh.observe(1,42,true,"",100);home_command.generation=fresh.generation;
        assert(fresh.queue(home_command,110));assert(fresh.dispatch(home_command,120));
    }
    t.observe(2,43,100,200,4);assert(t.token());
    // A continuous physical hold survives control locks, but no old command
    // permission survives them. Dispatch requires a fresh offer after release.
    const char* locks[]={"hand_mode_not_normal","training_attention","paused",
        "script_owns_creature","script_camera_or_unknown_scene","temple"};
    for(const char* reason:locks) {
        HeldTarget held;held.observe(1,42,100,200,3,true,true);
        const auto token=held.token();
        Policy p(true,true,true);p.actions(held_action_mask(3,true,true));
        p.held_target_generation=token;p.observe(1,42,true,"",100);
        Command c;c.version=6;c.action=26;c.reserved=token;
        c.epoch=1;c.generation=p.generation;c.offered_at=p.offered_at;
        assert(p.queue(c,105));
        held.observe(1,42,100,200,3,true,true);
        assert(held.token()==token && held.can_eat && held.can_bring_home);
        p.actions(0);p.observe(1,42,false,reason,110);
        assert(!p.dispatch(c,115));assert(!p.queue(c,116));
        p.actions(held_action_mask(3,true,true));p.observe(1,42,true,"",120);
        assert(!p.queue(c,125));
        c.generation=p.generation;c.offered_at=p.offered_at;
        assert(p.queue(c,130));assert(p.dispatch(c,135));
    }
    for (const auto& recipe:held_recipes) {
        Policy p(true,true,true);p.actions(held_action_mask(1,true));p.held_target_generation=3;
        p.observe(1,42,true,"",100);
        Command c;c.version=6;c.action=recipe.action;c.reserved=3;
        c.epoch=p.epoch;c.generation=p.generation;c.offered_at=p.offered_at;
        Policy legacy(true,true);legacy.actions(held_action_mask(1,true));legacy.held_target_generation=3;
        legacy.observe(1,42,true,"",100);assert(!legacy.queue(c,110));
        auto wrong=c;wrong.version=5;assert(!p.queue(wrong,110));
        wrong=c;wrong.action=13;assert(!p.queue(wrong,110));
        wrong=c;wrong.reserved=2;assert(!p.queue(wrong,110));
        assert(p.queue(c,110));p.held_target_generation=4;assert(!p.dispatch(c,120));
        p.observe(1,42,true,"",130);c.generation=p.generation;c.offered_at=p.offered_at;c.reserved=4;
        assert(p.queue(c,140));assert(p.dispatch(c,150));assert(!p.queue(c,160));
    }
}
