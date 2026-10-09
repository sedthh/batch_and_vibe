#include "../../native/world_actions.h"
#include "../../native/action_probe_policy.h"
#include "check.h"
#include <cstring>
using namespace bwv_probe;
int main() {
    WorldOffers offers;
    WorldEntry input[]={{0,0,{42,43,44,4,20},false},{0,1,{42,43,44,4,20},false},
        {0,2,{11,0,12,5,0},true}};
    offers.observe(input,3,1,11);assert(offers.count==3);
    const auto attack=offers.entries[0].token,poo=offers.entries[1].token;
    assert(attack && poo && attack!=poo);
    offers.observe(input,3,1,11);assert(offers.entries[0].token==attack);
    input[0].target.belief=77;offers.observe(input,3,1,11);
    assert(!offers.find(attack) && offers.find(poo));
    input[0].held_generation=7;input[0].held_spell=22;offers.observe(input,3,1,11);
    const auto held_offer=offers.entries[0].token;
    input[0].held_generation=8;offers.observe(input,3,1,11);
    assert(!offers.find(held_offer));
    const auto new_bubble_offer=offers.entries[0].token;
    input[0].held_spell=10;offers.observe(input,3,1,11);
    assert(!offers.find(new_bubble_offer));
    offers.observe(input,3,2,11);assert(!offers.find(poo));
    Policy p(true,true,true);p.world_tokens[0]=9;p.actions(1u<<18);p.observe(1,11,true,"",100);
    Command c;c.version=10;c.action=19;c.reserved=9;c.epoch=1;c.generation=p.generation;c.offered_at=100;
    assert(p.queue(c,110));p.world_tokens[0]=10;assert(!p.dispatch(c,120));
    // A held cast binds BOTH its destination and the bubble. Ordinary world
    // commands still require an empty hand; matching the world token is not enough.
    Policy bubble(true,true,true);bubble.world_tokens[0]=9;bubble.world_tokens[1]=10;
    bubble.world_held_generations[0]=7;bubble.held_target_generation=7;
    bubble.actions(1u<<18);bubble.observe(1,11,true,"",100);
    c.generation=bubble.generation;c.reserved=10;assert(!bubble.queue(c,110));
    c.reserved=9;assert(bubble.queue(c,110));assert(bubble.dispatch(c,120));
    Policy changed(true,true,true);changed.world_tokens[0]=9;
    changed.world_held_generations[0]=7;changed.held_target_generation=7;
    changed.actions(1u<<18);changed.observe(1,11,true,"",100);
    c.generation=changed.generation;assert(changed.queue(c,110));
    changed.held_target_generation=8;assert(!changed.dispatch(c,120));
    changed.held_target_generation=0;assert(!changed.world_target(9));
    Policy animation(true,true,true);animation.world_tokens[0]=9;
    animation.actions((1u<<18)|7);animation.observe(1,11,true,"",100);
    c.generation=animation.generation;assert(animation.queue(c,110));
    animation.actions((1u<<18)|(1u<<15)); // routine animation changed, world command still executable
    assert(animation.dispatch(c,120));
    Policy scene(true,true,true);scene.world_tokens[0]=9;scene.actions(1u<<18);scene.observe(1,11,true,"",100);
    c.generation=scene.generation;assert(scene.queue(c,110));
    scene.observe(1,11,false,"cutscene",115);scene.observe(1,11,true,"",120);
    assert(!scene.dispatch(c,121)); // a scene transition still revokes the queued command
    bool field_water=false,object_water=false,field_poo=false,social_dance=false;
    const auto& vomit=world_recipes[world_recipe_count-1];
    assert(!std::strcmp(vomit.name,"vomit") && !std::strcmp(vomit.label,"Puke"));
    assert(vomit.plan==50 && vomit.constructor==0x4A0280 && vomit.self && !vomit.kinds && !vomit.spell);
    assert(!std::strcmp(throw_kind_name(12),"field"));
    for(size_t i=0;i<world_recipe_count;++i) {
        const auto& r=world_recipes[i];assert(r.plan<328 && r.spell<=41 && r.constructor && (r.self||r.kinds));
        if(r.body_animation>=0)assert(r.body_animation>=200 && r.body_animation<=204 && r.plan==75 && r.self && !r.kinds && !r.spell);
        if(!std::strcmp(r.name,"cast_water_field")) {
            field_water=true;assert(r.plan==305 && r.spell==22 && r.constructor==0x4B6EA0 && r.kinds==(1u<<12));
        }
        if(!std::strcmp(r.name,"cast_water_object")) {
            object_water=true;assert(r.plan==238 && r.spell==22 && r.constructor==0x4B6EA0 && (r.kinds&(1u<<4)));
        }
        if(!std::strcmp(r.name,"poo_on")) {
            field_poo=true;assert(r.plan==19 && r.constructor==0x49ABB0 && (r.kinds&(1u<<12)));
        }
        if(!std::strcmp(r.name,"dance_with_villagers")) {
            social_dance=true;
            assert(r.plan==309 && r.constructor==0x49C9A0 && !r.self && !r.spell && r.kinds==(1u<<3));
            assert(!std::strcmp(r.label,"DanceAmorouslyWithVillagers"));
        }
        if(!std::strcmp(r.name,"attack") || !std::strcmp(r.name,"poo_on")
                || !std::strcmp(r.name,"cast_food") || !std::strcmp(r.name,"cast_water_object")
                || !std::strcmp(r.name,"cast_wood"))
            assert(r.kinds==all_object_kinds);
        if(!std::strncmp(r.name,"cast_creature_",14))assert(r.kinds==(1u<<5));
        assert(std::strcmp(r.name,"show_learned_lesson")); // same animation as scratch, not another gesture
        for(size_t j=0;j<i;++j)assert(std::strcmp(r.name,world_recipes[j].name));
    }
    assert(field_water && object_water && field_poo && social_dance);
    const auto& fish=world_recipes[0];
    assert(!std::strcmp(fish.name,"fish") && fish.plan==155 && fish.self && !fish.kinds);
    // Physical temple parts must not be rejected merely because the public kind
    // is temple. Only the actual Citadel/Flock containers lack the Object ABI.
    const auto fake_type=[](uintptr_t object,const char* name) {
        return (object==100 && !std::strcmp(name,".?AVCitadel@@"))
            || (object==200 && !std::strcmp(name,".?AVFlock@@"));
    };
    for(const auto& r:world_recipes) {
        for(unsigned kind=1;kind<=target_kind_count;++kind) {
            const ThrowPoint physical{300,400,500,kind,20};
            assert(world_target_supported(r,physical,fake_type)==bool(r.kinds&(1u<<kind)));
        }
        for(const ThrowPoint marker : {ThrowPoint{100,400,500,6,20},ThrowPoint{200,400,500,15,20}}) {
            const bool supported=(r.kinds&(1u<<marker.kind)) && (r.plan==122 || r.plan==49);
            assert(world_target_supported(r,marker,fake_type)==supported);
        }
        assert(!world_target_supported(r,{300,400,500,target_kind_count+1,20},fake_type));
    }
    assert(world_recipe_count*(known_target_capacity+1)<=WorldOffers::capacity);
    // A dense stand of trees must not remove a field or dispenser from choices.
    NearbyTargets nearby;
    for(uintptr_t i=1;i<=30;++i)nearby.add({i,100+i,200+i,1,double(i)});
    nearby.add({80,180,280,12,60});nearby.add({81,181,281,13,70});
    ThrowPoint targets[object_target_capacity]{};
    const auto count=nearby.select(all_object_kinds,targets);
    bool field=false,dispenser=false;
    for(size_t i=0;i<count;++i){field|=targets[i].kind==12;dispenser|=targets[i].kind==13;}
    assert(field && dispenser && count==10);
    assert(nearby.select(1u<<12,targets)==1 && targets[0].object==80);
    assert(nearby.select(portable_object_kinds,targets)==8);
    // The bounded generic sample may omit kinds; explicit focus must recover one.
    for(unsigned kind=2;kind<=target_kind_count;++kind)nearby.add({300+kind,400+kind,500+kind,kind,80});
    const auto diverse_count=nearby.select(all_object_kinds,targets);
    uint32_t represented=0;for(size_t i=0;i<diverse_count;++i)represented|=1u<<targets[i].kind;
    assert((represented&~all_object_kinds)==0 && diverse_count==object_target_capacity);
    KnownTargets focused;focused.focus_object=9999;
    for(uintptr_t i=1;i<=30;++i)focused.add({i,100+i,200+i,1,double(i)});
    focused.add({9999,19999,29999,1,900});
    ThrowPoint focus_targets[known_target_capacity]{};
    const auto focused_count=focused.select(all_object_kinds,focus_targets);
    bool recovered=false;
    for(size_t i=0;i<focused_count;++i)recovered|=focus_targets[i].object==9999;
    assert(recovered && focused_count<=known_target_capacity);
    const auto portable_count=focused.select_portable(portable_object_kinds,focus_targets);
    recovered=false;
    for(size_t i=0;i<portable_count;++i)recovered|=focus_targets[i].object==9999;
    assert(recovered && portable_count<=object_target_capacity);
    KnownTargets known;
    known.nearby=nearby;
    for(uintptr_t i=1;i<=30;++i)known.add({1000+i,2000+i,3000+i,1,120.+i});
    known.add({2000,4000,5000,12,600});known.add({2001,4001,5001,6,900});
    known.add({2002,4002,5002,12,known_target_distance+1});
    known.add({2003,4003,5003,12,NAN});
    ThrowPoint far_targets[known_target_capacity]{};
    const auto known_count=known.select(all_object_kinds,far_targets);
    assert(known_count==object_target_capacity+10);
    field=false;bool temple=false;
    for(size_t i=object_target_capacity;i<known_count;++i) {
        field|=far_targets[i].object==2000;temple|=far_targets[i].object==2001;
        assert(far_targets[i].distance>nearby_target_distance);
    }
    assert(field && temple);
    // Target identity survives changes in range/order, but not replacement.
    input[0].target.distance=600;offers.observe(input,3,2,11);
    const auto distant_token=offers.entries[0].token;
    input[0].target.distance=40;offers.observe(input,3,2,11);
    assert(offers.entries[0].token==distant_token);
    bool move=false;
    for(const auto& r:world_recipes)if(!std::strcmp(r.name,"move_to")) {
        move=true;assert(r.plan==122 && r.constructor==0x4A6EA0 && r.kinds==all_object_kinds);
    }
    assert(move);
}
