#pragma once
#include <cstdint>

namespace bwv_probe {
// One object's classification during a read-only operation. Read its vtable
// once, then compare type metadata. Other pointers (e.g. subtype info) are read
// afresh. Never retain this view across scans or native engine calls.
template<class Word,class Match> struct ObjectTypeView {
    uintptr_t object,table;
    Word word;
    Match match;
    bool operator()(uintptr_t candidate,const char* name) const {
        return match(candidate==object?table:word(candidate),name);
    }
};
template<class Word,class Match>
auto object_type_view(uintptr_t object,Word word,Match match) {
    return ObjectTypeView<Word,Match>{object,word(object),word,match};
}
}
