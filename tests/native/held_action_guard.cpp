#include "check.h"
#include "../../native/held_action_guard.h"
#include "../../native/action_probe_policy.h"
using namespace bwv_probe;
int main() {
    HeldTarget t;t.observe(1,42,100,200,1);auto first=t.token();assert(first);
    t.observe(1,42,100,200,1);assert(t.token()==first);
    t.observe(1,42,0,0,0);assert(!t.token());
    t.observe(1,42,100,200,1);assert(t.token()!=first); // Released and reacquired.
    auto old=t.token();t.observe(2,42,100,200,1);assert(t.token()!=old);
    old=t.token();t.observe(2,43,100,200,1);assert(t.token()!=old);
    assert((held_action_mask(1,true)&(1u<<13)) && !(held_action_mask(2)&(1u<<13)));
    assert(!held_action_mask(0) && held_action_mask(4)==((1u<<11)|(1u<<20)));
    for (uint32_t kind=2;kind<=10;++kind) assert(held_action_mask(kind)==((1u<<11)|(1u<<20)));
    for(uint32_t kind=1;kind<=10;++kind) {
        assert(held_action_mask(kind,true)&(1u<<13));
        assert(!(held_action_mask(kind,false)&(1u<<13)));
    }
    old=t.token();t.observe(2,43,100,200,1,true);assert(t.can_eat && t.token()!=old);
    old=t.token();t.observe(2,43,100,200,1,false);assert(!t.can_eat && t.token()!=old);
    assert(!held_recipe(13)); // Retired examination command.
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
    t.observe(2,43,100,200,4);assert(t.token());
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
