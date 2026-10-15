#include "check.h"
#include "../../native/object_type_view.h"
#include "../../native/object_types.h"
#include "../../native/building_types.h"
#include <cstring>
#include <unordered_map>
int main(){
    using namespace bwv_probe;
    std::unordered_map<uintptr_t,uintptr_t> values{{1,10},{2,20},{1+0x28,2},{2+0x120,399}};
    unsigned reads=0;
    auto word=[&](uintptr_t p){++reads;return values[p];};
    auto match=[](uintptr_t table,const char* name){
        return (table==10 && !std::strcmp(name,".?AVMobileStatic@@"))
            || (table==20 && !std::strcmp(name,".?AVGMobileStaticInfo@@"))
            || (table==30 && !std::strcmp(name,".?AVWorkshop@@"));
    };
    auto check=object_type_view(1,word,match);
    for(unsigned i=0;i<100;++i){assert(check(1,".?AVMobileStatic@@"));assert(!check(1,".?AVTree@@"));}
    assert(reads==1); // Repeated names do not reread the object's vtable.
    assert(object_kind(1,check,word)==7); // Subtype reads still happen.
    values[2+0x120]=56;
    assert(object_kind(1,check,word)==0);
    assert(building_kind(1,check,word)==25);
    values[2]=0;
    assert(building_kind(1,check,word)==0); // Other pointers are never frozen.
    values[1]=30;
    auto replacement=object_type_view(1,word,match);
    assert(building_kind(1,replacement,word)==19);
    assert(!replacement(1,".?AVMobileStatic@@"));
    values[1]=0;
    auto removed=object_type_view(1,word,match);
    assert(!removed(1,".?AVWorkshop@@"));
}
