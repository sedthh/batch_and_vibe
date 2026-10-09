#pragma once
#include <cmath>
#include <initializer_list>
namespace bwv_probe {
// These are independent native physical limits, not guesses from species/age.
inline const char* pickup_limit_reason(double creature_radius,double object_radius,
        double pickup_reach,double object_weight,double max_weight) {
    for(double v:{creature_radius,object_radius,pickup_reach,object_weight,max_weight})
        if(!std::isfinite(v) || v<0)return nullptr;
    if(creature_radius+object_radius>pickup_reach)return "too_large_for_current_size";
    if(object_weight>max_weight)return "too_heavy_to_lift";
    return nullptr;
}
}
