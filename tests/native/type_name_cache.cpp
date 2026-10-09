#include "check.h"
#include "../../native/type_name_cache.h"
int main() {
    bwv_probe::TypeNameCache cache;
    unsigned reads=0,fallbacks=0;
    auto load=[&](uintptr_t table,char* name,size_t size) {
        ++reads;
        if(table==999)return false;
        if(table==998){std::memset(name,'x',size);return true;}
        std::strcpy(name,table%2?"tree":"villager");return true;
    };
    auto fallback=[&](uintptr_t,const char* expected) {++fallbacks;return !std::strcmp(expected,"fallback");};
    for(unsigned i=0;i<512;++i) {
        assert(cache.matches(1,"tree",load,fallback));
        assert(!cache.matches(1,"rock",load,fallback));
        assert(cache.matches(2,"villager",load,fallback));
    }
    assert(reads==2 && fallbacks==0);
    assert(cache.matches(999,"fallback",load,fallback));
    assert(cache.matches(998,"fallback",load,fallback));
    assert(cache.count==2);
    for(uintptr_t table=3;table<200;++table)cache.matches(table,"fallback",load,fallback);
    assert(cache.count==cache.capacity && fallbacks>2);
    bwv_probe::TypeNameCache next_hook;
    assert(next_hook.matches(1,"tree",load,fallback));
    assert(next_hook.count==1);
}
