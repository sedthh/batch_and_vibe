#pragma once
#include <windows.h>
#include <cstdint>
#include <string>

namespace bwv_observer {
// Fixed-size counters only; no growing trace or per-hook allocation.
struct WorkTiming {
    uint64_t calls=0, total_us=0, last_us=0, max_us=0;
    void record(uint64_t elapsed) {
        ++calls;last_us=elapsed;total_us+=elapsed;
        if(elapsed>max_us)max_us=elapsed;
    }
    std::string json() const {
        return "{\"calls\":"+std::to_string(calls)+",\"total_us\":"+std::to_string(total_us)
            +",\"last_us\":"+std::to_string(last_us)+",\"max_us\":"+std::to_string(max_us)+"}";
    }
};
struct TimedWork {
    WorkTiming& target;
    LARGE_INTEGER start{};
    explicit TimedWork(WorkTiming& timing):target(timing){QueryPerformanceCounter(&start);}
    ~TimedWork(){
        LARGE_INTEGER end{},frequency{};
        QueryPerformanceCounter(&end);QueryPerformanceFrequency(&frequency);
        if(frequency.QuadPart>0 && end.QuadPart>=start.QuadPart)
            target.record(static_cast<uint64_t>((end.QuadPart-start.QuadPart)*1000000/frequency.QuadPart));
    }
};
}
