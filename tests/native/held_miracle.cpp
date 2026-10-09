#include "../../native/held_miracle.h"
#include "check.h"
#include <cstring>
#include <map>
using namespace bwv_probe;
int main() {
    constexpr uintptr_t object=0x1000,table=0x2000,magic=0x3000,info=0xD9D678+2*0x190;
    std::map<uintptr_t,uint32_t> memory={{object,table},{table+0xAC,0x55D130},
        {0xBE4798,0x507B90},{0xBE4828,0x507BE0},{object+0x68,2},{object+0x78,0},
        {info+0x124,22},{info+0x128,23},{0xD37D10+22*4,magic},
        {magic+0x48,0},{magic+0x4C,1},{magic+0x44,1},{magic+0x50,0}};
    bool one_off=true;
    auto type=[&](uintptr_t p,const char* name){return (p==object && one_off && !std::strcmp(name,".?AVOneOffSpellSeed@@"))
        || (p==info && !std::strcmp(name,".?AVGSpellSeedInfo@@"));};
    auto word=[&](uintptr_t p){auto i=memory.find(p);return i==memory.end()?0:i->second;};
    auto read=[&](uintptr_t p,uint32_t& v){auto i=memory.find(p);if(i==memory.end())return false;v=i->second;return true;};
    auto inspect=[&](){return inspect_held_miracle(object,type,word,read);};
    auto r=inspect();assert(r.verified && r.spell==22 && r.bubble_spell==23 && r.categories==6);
    one_off=false;assert(!inspect().verified);one_off=true;
    memory[object+0x68]=30;assert(!inspect().verified);memory[object+0x68]=2;
    memory[object+0x78]=UINT32_MAX;assert(inspect().bubble_spell==22);
    memory[object+0x78]=3;assert(!inspect().verified);memory[object+0x78]=0;
    memory[info+0x124]=0;assert(!inspect().verified);memory[info+0x124]=22;
    memory[magic+0x4C]=2;assert(!inspect().verified);memory[magic+0x4C]=1;
    memory.erase(magic+0x50);assert(!inspect().verified);memory[magic+0x50]=0;
    memory[0xBE4798]=0;assert(!inspect().verified);
}
