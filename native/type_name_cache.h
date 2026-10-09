#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>

namespace bwv_probe {
// RTTI class names belong to vtables, not object addresses. Cache names only
// for this hook, never mutable object state. Unknown/malformed reads fall back
// to the original bounded check; capacity affects speed, not eligibility.
struct TypeNameCache {
    static constexpr size_t capacity=96, name_size=96;
    struct Entry { uintptr_t table=0;char name[name_size]{}; };
    Entry entries[capacity]{};
    size_t count=0;
    template<class Load, class Fallback>
    bool matches(uintptr_t table, const char* expected, Load load, Fallback fallback) {
        for(size_t i=0;i<count;++i)if(entries[i].table==table)
            return !std::strcmp(entries[i].name,expected);
        if(count<capacity) {
            auto& entry=entries[count];
            if(load(table,entry.name,name_size) && std::memchr(entry.name,0,name_size)) {
                entry.table=table;++count;
                return !std::strcmp(entry.name,expected);
            }
        }
        return fallback(table,expected);
    }
};
}
