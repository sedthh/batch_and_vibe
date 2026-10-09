#include "../../native/object_movement_guard.h"
#include "check.h"
#include <limits>
using namespace bwv_probe;
int main() {
    assert(tree_has_approach_space(25,2,14));
    assert(!tree_has_approach_space(17,2,14));
    assert(!tree_has_approach_space(25,2,30));
    assert(!tree_has_approach_space(25,-2,14));
    assert(!tree_has_approach_space(25,2,std::numeric_limits<double>::quiet_NaN()));
    ObjectArrival arrival;
    arrival.sample_radius(2,1);assert(!arrival.reached(1,true,true,true,true));
    arrival.bind_allowance(14);arrival.sample_radius(2,1);
    assert(arrival.radius_verified && arrival.confirmed_radius<17.6);
    assert(arrival.reached(16.6,true,true,true,true));
    assert(!arrival.reached(18,true,true,true,true));
    assert(!arrival.reached(arrival.confirmed_radius,true,true,true,true));
    assert(!arrival.reached(16.6,false,true,true,true));
    assert(!arrival.reached(16.6,true,false,true,true));
    assert(!arrival.reached(16.6,true,true,false,true));
    assert(!arrival.reached(16.6,true,true,true,false));
    assert(!arrival.reached(-1,true,true,true,true));
    assert(!arrival.reached(std::numeric_limits<double>::quiet_NaN(),true,true,true,true));
    arrival.sample_radius(2,2);assert(!arrival.reached(1,true,true,true,true)); // Rock contract unknown.
    arrival.sample_radius(1,1);assert(!arrival.reached(16.6,true,true,true,true)); // Shrinking changes radius.
    arrival.sample_radius(0,1);assert(!arrival.radius_verified);
    arrival.bind_allowance(501);arrival.sample_radius(2,1);assert(!arrival.radius_verified);
    arrival.bind_allowance(std::numeric_limits<double>::infinity());assert(!arrival.allowance_verified);
    ObjectTarget t;
    ObjectPoint p{100,200,300,1,50,60,16};
    t.observe(p,1,10,20);auto token=t.token();assert(token);
    p.distance=12;t.observe(p,1,10,20);assert(t.token()==token);
    p.x+=.01;t.observe(p,1,10,20);assert(t.token()!=token);
    token=t.token();p.x-=.01;t.observe(p,1,10,20);assert(t.token()!=token);
    token=t.token();t.observe({},1,10,20);assert(!t.token());
    t.observe(p,1,10,20);assert(t.token()!=token);
    token=t.token();p.belief++;t.observe(p,1,10,20);assert(t.token()!=token);
    token=t.token();p.table++;t.observe(p,1,10,20);assert(t.token()!=token);
    token=t.token();t.observe(p,2,10,20);assert(t.token()!=token);
    token=t.token();t.observe(p,2,11,20);assert(t.token()!=token);
    token=t.token();t.observe(p,2,11,21);assert(t.token()!=token);
    p.distance=7; t.observe(p,2,11,21);assert(!t.token());
    p.distance=31;t.observe(p,2,11,21);assert(!t.token());
    p.distance=12;p.kind=3;t.observe(p,2,11,21);assert(!t.token());
    p.kind=2;p.x=std::numeric_limits<double>::quiet_NaN();assert(!object_point_valid(p));
    p.x=50;t.generation=UINT32_MAX;t.observe(p,3,11,21);assert(!t.token());
}
