#include "../../native/individual_gesture.h"
#include "check.h"
using namespace bwv_probe;
int main() {
    assert(individual_gesture_argument(201));
    assert(!individual_gesture_argument(23)); // facial laugh must never be used
    assert(!individual_gesture_argument(199));
    assert(!individual_gesture_argument(205));
    assert(individual_gesture_sequence(1,0,10,60));
    assert(!individual_gesture_sequence(2,0,10,60));
    assert(!individual_gesture_sequence(1,1,10,60));
    assert(!individual_gesture_sequence(1,0,132,60));
    assert(!individual_gesture_sequence(1,0,10,29));
}
