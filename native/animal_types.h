#pragma once
#include <cstdint>
#include <initializer_list>
namespace bwv_probe {
// Match exact wildlife classes, never puzzle pieces or the player's Creature.
template<class TypeCheck> bool animal_object(uintptr_t object, TypeCheck check) {
    for(const char* name:{".?AVAnimal@@", ".?AVCow@@", ".?AVSheep@@", ".?AVHorse@@",
            ".?AVPig@@", ".?AVGoat@@", ".?AVWolf@@", ".?AVLion@@", ".?AVTiger@@",
            ".?AVLeopard@@", ".?AVZebra@@", ".?AVTortoise@@", ".?AVCrow@@",
            ".?AVDove@@", ".?AVPigeon@@"})
        if(check(object,name))return true;
    return false;
}
}
