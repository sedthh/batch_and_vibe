#include "../../native/individual_gesture.h"
#include "check.h"
using namespace bwv_probe;
int main() {
    for(int animation=217;animation<=221;++animation)
        assert(individual_gesture_argument(animation));
    // Regression: the old range was concurrent nod/shake/yawn/thirst/squirt.
    for(int animation=200;animation<=204;++animation)
        assert(!individual_gesture_argument(animation));
    assert(!individual_gesture_argument(23)); // facial laugh must never be used
    assert(!individual_gesture_argument(-1));
    assert(!individual_gesture_argument(216));
    assert(!individual_gesture_argument(222));
    assert(individual_gesture_sequence(1,0,10,60));
    assert(!individual_gesture_sequence(2,0,10,60));
    assert(!individual_gesture_sequence(1,1,10,60));
    assert(!individual_gesture_sequence(1,0,132,60));
    assert(!individual_gesture_sequence(1,0,10,29));
}
