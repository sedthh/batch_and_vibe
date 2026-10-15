// Synthetic linked beliefs exercise the actual native target scanner.
#include <cstdint>
#include <cstring>
#include <string>
#include <unordered_map>
#include <initializer_list>
#include "check.h"
namespace bwv_probe {
std::unordered_map<uintptr_t,uint32_t> words;
std::unordered_map<uintptr_t,std::string> classes;
std::unordered_map<uintptr_t,unsigned> bases;
size_t word_reads=0;
unsigned world_bases(uintptr_t p) {return bases[p];}
bool type(uintptr_t p,const char* name) {
    const auto found=classes.find(p);
    return found!=classes.end() && found->second==name;
}
// Synthetic class identities double as their vtables; distinct addresses may
// change classes between scans, just as objects can be replaced in the game.
bool type_table(uintptr_t table,const char* name){return type(table,name);}
uint32_t word(uintptr_t p) {
    ++word_reads;
    if(classes.find(p)!=classes.end())return static_cast<uint32_t>(p);
    const auto i=words.find(p);return i==words.end()?0:i->second;
}
template<class T> bool read(uintptr_t p,T& out) {
    const auto i=words.find(p);
    if(i==words.end() || sizeof(T)>4)return false;
    std::memcpy(&out,&i->second,sizeof(T));return true;
}
bool range(uintptr_t,size_t) {return false;}
bool movement_belief(uintptr_t p) {return type(p,".?AVCreatureBelief@@");}
}
#include "../../native/throw_targets_live.h"
#include "../../native/world_actions.h"
using namespace bwv_probe;
constexpr uintptr_t mental=0x100000,creature=0x200000;
uintptr_t node(size_t i) {return 0x400000+i*0x100;}
uintptr_t object(size_t i) {return 0x800000+i*0x100;}
void fixture(size_t count) {
    words.clear();classes.clear();bases.clear();
    classes[mental+0x2208]=".?AVCreatureBeliefs@@";
    words[creature+0x14]=words[creature+0x18]=0;
    for(uintptr_t offset:{0x2210u,0x2224u}) {
        const auto list=mental+offset;
        classes[list]=".?AVCreatureBeliefList@@";
        words[list+8]=words[list+12]=words[list+16]=0;
    }
    const auto list=mental+0x2210;
    words[list+8]=count?node(0):0;words[list+12]=count?node(count-1):0;
    words[list+16]=count;
    for(size_t i=0;i<count;++i) {
        classes[node(i)]=".?AVCreatureBelief@@";
        words[node(i)+8]=i+1<count?node(i+1):0;
        words[node(i)+12]=i?node(i-1):0;
        words[node(i)+0x30]=object(i);
        classes[object(i)]=".?AVTree@@";
        words[object(i)]=0x900000;
        words[object(i)+0x14]=(20+i)*65536/10;words[object(i)+0x18]=0;
    }
}
int main() {
    fixture(3);
    classes[object(0)]=".?AVUnmappedThing@@";bases[object(0)]=3;
    classes[object(1)]=".?AVUnmappedPlace@@";bases[object(1)]=1;
    classes[object(2)]=".?AVFragment@@";bases[object(2)]=3;
    KnownTargets generic;
    assert(inspect_target_pool(creature,mental,0,generic));
    ThrowPoint unknown[known_target_capacity]{};
    assert(generic.select(all_object_kinds,unknown)==2);
    assert(unknown[0].kind==29 && !unknown[0].position_only);
    assert(unknown[1].kind==29 && unknown[1].position_only);
    for(const auto& recipe:world_recipes) {
        if(!(recipe.kinds&(1u<<29)))continue;
        assert(world_target_supported(recipe,unknown[0],type));
        assert(world_target_supported(recipe,unknown[1],type)==(recipe.plan==122 || recipe.plan==49));
    }
    fixture(3);
    for(size_t i=0;i<3;++i)classes[object(i)]=".?AVVillager@@";
    words[object(0)+0x8C]=15; // Dead person, still a Villager.
    words[object(1)+0x25]=4; // Scripted actor.
    KnownTargets people;
    assert(inspect_target_pool(creature,mental,0,people));
    assert(people.select(all_object_kinds,unknown)==3);
    assert(unknown[0].person_state==15 && target_importance(unknown[0])==3);
    assert(unknown[1].important_person && target_importance(unknown[1])==4);
    assert(target_importance(unknown[2])==5);
    // A selected workshop beyond the former cutoff must survive dense trees.
    for(size_t count:{0u,512u,513u,723u,4096u}) {
        fixture(count);KnownTargets pool;
        if(count) {
            classes[object(count-1)]=".?AVWorkshop@@";
            pool.focus_object=object(count-1);
        }
        assert(inspect_target_pool(creature,mental,0,pool));
        if(!count)continue;
        assert(pool.focus.object==object(count-1) && pool.focus.kind==19);
        const WorldRecipe* wood=nullptr;
        for(const auto& r:world_recipes)if(!std::strcmp(r.name,"cast_wood"))wood=&r;
        assert(wood);
        ThrowPoint targets[known_target_capacity]{};
        const auto found=pool.select(wood->kinds,targets);bool selected=false;
        for(size_t i=0;i<found;++i)if(targets[i].object==object(count-1)) {
            assert(world_target_supported(*wood,targets[i],type));selected=true;
        }
        assert(selected);
        classes[object(count-1)]=".?AVStoragePit@@";
        KnownTargets storage;storage.focus_object=object(count-1);
        assert(inspect_target_pool(creature,mental,0,storage));
        assert(storage.focus.kind==16 && world_target_supported(*wood,storage.focus,type));
    }
    // Refreshes replace prior targets and repeat the same amount of read work.
    // A long session must not append yesterday's objects to today's scan.
    fixture(723);KnownTargets repeated;
    word_reads=0;assert(inspect_target_pool(creature,mental,0,repeated));
    const auto reads_per_scan=word_reads;
    ThrowPoint initial[known_target_capacity]{};
    const auto initial_count=repeated.select(all_object_kinds,initial);
    for(unsigned iteration=0;iteration<100;++iteration) {
        word_reads=0;assert(inspect_target_pool(creature,mental,0,repeated));
        assert(word_reads==reads_per_scan);
        ThrowPoint current[known_target_capacity]{};
        assert(repeated.select(all_object_kinds,current)==initial_count);
        for(size_t i=0;i<initial_count;++i)assert(current[i].object==initial[i].object);
    }
    fixture(1);classes[object(0)]=".?AVStoragePit@@";
    assert(inspect_target_pool(creature,mental,0,repeated));
    ThrowPoint replaced[known_target_capacity]{};
    assert(repeated.select(all_object_kinds,replaced)==1 && replaced[0].kind==16);
    fixture(0);assert(inspect_target_pool(creature,mental,0,repeated));
    assert(repeated.select(all_object_kinds,replaced)==0);
    // No arbitrary count ceiling, but invalid lists still terminate and fail.
    for(int corruption=0;corruption<5;++corruption) {
        fixture(723);KnownTargets pool;
        if(corruption==0)words[mental+0x2210+16]=UINT32_MAX;
        if(corruption==1)words[node(722)+8]=node(400); // cycle
        if(corruption==2)words[node(600)+12]=node(12); // broken backlink
        if(corruption==3)words[mental+0x2210+12]=node(12); // wrong tail
        if(corruption==4)classes[node(600)]=".?AVTree@@";
        assert(!inspect_target_pool(creature,mental,0,pool));
        ThrowPoint out[known_target_capacity]{};assert(pool.select(all_object_kinds,out)==0);
    }
}
