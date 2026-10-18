#include "../../native/teleport_travel.h"
#include "../../native/world_actions.h"
#include "check.h"
#include <cstring>
#include <map>
using namespace bwv_probe;
int main() {
    std::map<uintptr_t,uint32_t> memory;
    const uintptr_t player=0x10000,a=0x20000,b=0x30000,c=0x40000;
    memory[player+0xA58]=a;memory[player+0xA5C]=3;
    for(auto gate:{a,b,c}) {
        memory[gate]=0x92C110;memory[gate+0xA0]=player;
        memory[gate+0x14]=(gate/a)*65536;memory[gate+0x18]=65536;
        memory[gate+0x1C]=0;
    }
    memory[a+0x88]=b;memory[b+0x88]=c;memory[c+0x88]=0;
    const auto type=[&](uintptr_t p,const char* name) {
        return p==player ? !std::strcmp(name,".?AVGPlayer@@") :
            memory.count(p) && !std::strcmp(name,".?AVMagicTeleport@@");
    };
    const auto read=[&](uintptr_t p,auto& value) {
        auto i=memory.find(p);if(i==memory.end())return false;
        static_assert(sizeof(value)==4);std::memcpy(&value,&i->second,4);return true;
    };
    TeleportNetwork network;
    assert(read_teleport_network(player,5120,5120,read,type,network));
    assert(network.count==3);
    TeleportRoute route;
    assert(network.route(c,10,10,route) && route.entrance.object==a && route.destination.object==c);
    assert(network.route(a,20,10,route) && route.entrance.object==c);
    assert(!network.route(0x50000,10,10,route)); // no silent different destination
    memory[b+0xA0]=0x50000;assert(!read_teleport_network(player,5120,5120,read,type,network));
    assert(network.count==0);memory[b+0xA0]=player;
    memory[c+0x88]=a;assert(!read_teleport_network(player,5120,5120,read,type,network));
    memory[c+0x88]=0;memory[player+0xA5C]=2;
    assert(!read_teleport_network(player,5120,5120,read,type,network));
    memory[player+0xA5C]=1;memory[a+0x88]=0;
    assert(read_teleport_network(player,5120,5120,read,type,network));
    assert(!network.route(a,10,10,route)); // lone gate cannot be used
    memory[a+0x14]=0;assert(!read_teleport_network(player,5120,5120,read,type,network));
    memory[a+0x14]=65536;memory[a+0x1C]=0x7fc00000;
    assert(!read_teleport_network(player,5120,5120,read,type,network));
    const uintptr_t reaction=0x50000;
    const auto reaction_type=[&](uintptr_t p,const char* name) {
        return p==reaction ? !std::strcmp(name,".?AVReaction@@") : type(p,name);
    };
    // Route registrations are retained only for the entrance's own reaction.
    assert(!teleport_reaction(a,read,reaction_type));
    memory[a+0x94]=reaction;memory[reaction+0x14]=a;memory[reaction+0x24]=20;
    assert(teleport_reaction(a,read,reaction_type)==reaction);
    assert(!teleport_reaction(a,read,type));
    memory[reaction+0x14]=b;assert(!teleport_reaction(a,read,reaction_type));
    memory[reaction+0x14]=a;memory[reaction+0x24]=19;
    assert(!teleport_reaction(a,read,reaction_type));
    size_t travel=0,attack=0;
    for(size_t i=0;i<world_recipe_count;++i) {
        if(!std::strcmp(world_recipes[i].name,"use_teleport"))travel=i;
        if(!std::strcmp(world_recipes[i].name,"attack"))attack=i;
    }
    const auto& recipe=world_recipes[travel];
    assert(recipe.plan==134 && recipe.constructor==0x4A9120 && !recipe.spell && !recipe.self);
    WorldOffers offers;
    WorldEntry exit{0,static_cast<uint32_t>(travel),{b,0,0x92C110,30,100},false};
    offers.observe(&exit,1,1,99);assert(offers.count==1);
    const auto token=offers.entries[0].token;
    exit.target.object=c;offers.observe(&exit,1,1,99);assert(!offers.find(token));
    exit.recipe=attack;offers.observe(&exit,1,1,99);assert(offers.count==0);
}
