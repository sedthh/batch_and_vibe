#include "../../native/miracle_actions.h"
#include "../../native/action_probe_policy.h"
#include "check.h"
#include <cstdio>
using namespace bwv_probe;
int main() {
    assert(miracle_recipe(17)->spell==10 && miracle_recipe(18)->spell==22);
    assert(miracle_recipe(17)->plan==58 && miracle_recipe(18)->plan==304);
    assert(!miracle_recipe(19));
    assert(!miracle_learning_observed(0) && miracle_learning_observed(1));
    assert(!miracle_learning_observed(0xffffffff));
    UnlearnedMiracleTest normal;
    assert(!normal.permits(186,9,0));
    UnlearnedMiracleTest qa{true};
    assert(qa.permits(186,9,0));
    assert(!qa.permits(185,8,0) && !qa.permits(47,7,0));
    assert(!qa.permits(186,9,1) && !qa.permits(186,9,0xffffffff));
    assert(!qa.consume(185,8,0) && !qa.used);
    assert(qa.consume(186,9,0) && qa.used);
    assert(!qa.permits(186,9,0) && !qa.consume(186,9,0));
    Policy p(true,true,true);p.actions((1u<<16)|(1u<<17));p.observe(1,42,true,"",100);
    Command c;c.version=9;c.epoch=p.epoch;c.generation=p.generation;c.offered_at=p.offered_at;c.action=18;c.reserved=7;
    assert(!p.queue(c,110)); // unrelated or absent tree token
    p.water_tokens[0]=7;
    c.version=8;assert(!p.queue(c,110));
    c.version=9;assert(p.queue(c,110));
    p.water_tokens[0]=9;assert(!p.dispatch(c,120)); // no retarget after queue
    p.observe(1,42,true,"",130);c.generation=p.generation;c.offered_at=p.offered_at;c.action=17;
    assert(!p.queue(c,140)); // Heal self takes no object token
    c.reserved=0;assert(p.queue(c,140));assert(p.dispatch(c,150));
    Policy old(true,true,false);old.actions(1u<<16);old.observe(1,42,true,"",100);
    c.generation=old.generation;c.offered_at=100;assert(!old.queue(c,110));
    std::puts("Miracle policy passed: version, learning, explicit target and dispatch identity");
}
