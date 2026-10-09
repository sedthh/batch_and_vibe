#include "../../native/action_catalog.h"
#include "../../native/action_probe_policy.h"
#include "check.h"
#include <cstring>
#include <cstdio>
using namespace bwv_probe;
int main() {
    uint32_t seen=0;
    for (const auto& recipe:self_recipes) {
        const uint32_t bit=1u<<(recipe.action-1);
        assert(!(seen&bit));seen|=bit;
        assert(recipe.plan<328 && recipe.name && recipe.runtime_name && recipe.constructor);
        assert(self_recipe(recipe.action)==&recipe);
        Policy p(true,true);p.actions(bit);p.observe(1,42,true,"",100);
        Command c;c.version=5;c.action=recipe.action;c.epoch=1;c.generation=p.generation;c.offered_at=100;
        assert(p.queue(c,200));assert(p.dispatch(c,210));
        Policy old(true);old.actions(bit);old.observe(1,42,true,"",100);c.generation=old.generation;
        assert(!old.queue(c,200));
        Policy changed(true,true);changed.actions(bit);changed.observe(1,42,true,"",100);
        c.generation=changed.generation;assert(changed.queue(c,200));
        changed.observe(1,42,false,"player_control",210);assert(!changed.dispatch(c,220));
        Policy expired(true,true);expired.actions(bit);expired.observe(1,42,true,"",100);
        c.generation=expired.generation;expired.observe(1,42,true,"",30200);assert(!expired.queue(c,30200));
    }
    assert(seen==self_recipe_mask());assert(!self_recipe(0));assert(!self_recipe(12));
    // A queued hand approach survives sleep/wake capability transitions, but
    // still loses permission if its own capability disappears.
    for (bool lose_hand : {false,true}) {
        Policy hand(true,true);hand.target_generation=5;hand.actions(8|4);
        hand.observe(1,42,true,"",100);
        Command c;c.version=2|Command::interrupt_flag;c.action=3;c.reserved=5;
        c.epoch=1;c.generation=hand.generation;c.offered_at=100;
        assert(hand.queue(c,110));hand.actions(lose_hand?8:7);
        assert(hand.dispatch(c,120)==!lose_hand);
    }
    Activity a{true,0,0,false};a.current_subaction=99;
    for (uint32_t plan=0;plan<328;++plan) {
        assert(catalog_activity_actions(a,plan,3)==7);
        a.controller_mode=1;assert(catalog_activity_actions(a,plan,3)==7);a.controller_mode=0;
        assert(catalog_activity_actions(a,plan,24)==7);
        assert(catalog_activity_actions(a,plan,26)==7);
        assert(catalog_activity_actions(a,plan,33)==7);
    }
    assert(!catalog_activity_actions(a,328,3));
    a.sleep_in_sequence=true;assert(!catalog_activity_actions(a,55,3));
    a.current_subaction=132;a.animation=29;a.controller_mode=3;
    assert(catalog_activity_actions(a,313,8)==8);
    a.controller_mode=4;assert(!catalog_activity_actions(a,313,8));
    a.sleep_in_sequence=false;a.animation=80;a.controller_mode=2;assert(catalog_activity_actions(a,72,3)==7);
    a.controller_mode=38;assert(!catalog_activity_actions(a,72,3));
    std::puts("Named action catalog: packet binding, legacy rejection and plan-independent interruption passed");
}
