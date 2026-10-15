// A standalone x86 host exercises the bridge without the game.
#include <windows.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <initializer_list>

int noop() { return 0; }
int enter_count = 0, leave_count = 0;
int original_enter(uint32_t slot) { ++enter_count; return 1000 + slot; }
int original_leave(uint32_t slot) { ++leave_count; return 2000 + slot; }
struct Entry { void* function; uint32_t inputs, outputs, reserved; char name[128]; };
int main(int argc,char**) {
    HMODULE module = LoadLibraryW(L"ScriptLibraryR.dll");
    if (!module) { std::printf("load error %lu\n", GetLastError()); return 1; }
    auto version = reinterpret_cast<int(*)()>(GetProcAddress(module, "Version"));
    auto init = reinterpret_cast<int(*)(int,void*,void*,void*,void*,int,void*)>(GetProcAddress(module, "Initialise"));
    auto tick = reinterpret_cast<int(*)(int,int)>(GetProcAddress(module, "LookIn"));
    auto code_size = reinterpret_cast<int(*)()>(GetProcAddress(module, "CodeSize"));
    auto restore = reinterpret_cast<int(*)(int,const char*)>(GetProcAddress(module, "RestoreState"));
    if (!version || !init || !tick || !code_size || !restore) {
        std::printf("exports %p %p %p %p %p error %lu\n", version, init, tick, code_size, restore, GetLastError());
        return 2;
    }
    if (version() != 7 || code_size() != 1234) return 3;
    auto pop = reinterpret_cast<uint32_t(*)(uint32_t*)>(GetProcAddress(module, "POP"));
    auto push = reinterpret_cast<int(*)(uint32_t,uint32_t)>(GetProcAddress(module, "PUSH"));
    if (!pop || !push) return 11;
    const uint32_t words[] = {158, 0, 0x80000000, 0x3f800000, 0x7f800000, 0x7fa12345, 0xdeadbeef};
    for (uint32_t type : {2u, 4u}) {
        for (uint32_t word : words) {
            if (push(word,type) != 719) return 12;
            uint32_t actual_type = 0;
            const uint32_t actual_word = pop(&actual_type);
            if (actual_word != word || actual_type != type || pop(nullptr) != word) {
                std::printf("operand ABI mismatch: expected %08x, got %08x\n", word, actual_word);
                return 13;
            }
        }
    }
    // Force one entry across adjacent committed regions with different protection.
    auto* memory = static_cast<unsigned char*>(VirtualAlloc(nullptr, 8192, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    if (!memory) return 7;
    auto* table = reinterpret_cast<Entry*>(memory + 4096 - 2 * sizeof(Entry) - 20);
    std::strcpy(table[1].name, "SYNTHETIC_RESERVED");
    table[2].function = reinterpret_cast<void*>(&noop);
    std::strcpy(table[2].name, "SYNTHETIC_POSITION");
    table[3].function = reinterpret_cast<void*>(&noop);
    table[3].outputs = 1;
    std::strcpy(table[3].name, "START_DIALOGUE"); // Interface name, synthetic implementation.
    DWORD previous = 0;
    if (!VirtualProtect(memory + 4096, 4096, PAGE_READONLY, &previous)) return 8;
    if (init(41,table,nullptr,reinterpret_cast<void*>(&original_enter),reinterpret_cast<void*>(&original_leave),1,nullptr) != 42) return 4;
    if(argc>1) {
        auto vm=GetModuleHandleW(L"_bwv_original_vm.dll");
        auto reads=reinterpret_cast<int(*)()>(GetProcAddress(vm,"SyntheticGlobalReads"));
        auto metadata=reinterpret_cast<int(*)()>(GetProcAddress(vm,"SyntheticMetadataReads"));
        if(!reads || !metadata)return 20;
        for(int i=0;i<6;++i){if(tick(40,2)!=42)return 21;Sleep(50);}
        if(reads()!=0 || metadata()!=0)return 22;
        std::puts("idle_ready");std::fflush(stdout);
        int stopped_reads=0,stopped_metadata=0;
        for(int i=0;i<100;++i){
            if(tick(40,2)!=42)return 23;
            if(i==80){stopped_reads=reads();stopped_metadata=metadata();}
            Sleep(50);
        }
        if(!stopped_reads || reads()!=stopped_reads || metadata()!=stopped_metadata)return 24;
        if(enter_count!=106 || leave_count!=106)return 25;
        std::puts("idle_suspended");std::fflush(stdout);
        for(int i=0;i<30;++i){if(tick(40,2)!=42)return 26;Sleep(50);}
        if(reads()<=stopped_reads)return 27;
        if(enter_count!=136 || leave_count!=136)return 28;
        std::puts("idle_resumed");return 0;
    }
    std::printf("ready %lu\n", GetCurrentProcessId()); std::fflush(stdout);
    for (int i = 0; i < 100; ++i) {
        if (tick(40,2) != 42) return 5;
        if (i == 40) {
            std::puts("trace_full"); std::fflush(stdout);
            Sleep(300);
            if (restore(17,"synthetic") != 17) return 6;
            std::puts("restored"); std::fflush(stdout);
            Sleep(700); // Test the invalidation before the next scheduler tick.
        }
        Sleep(50);
    }
    if (enter_count != 100 || leave_count != 100) return 9;
    if (init(41,table,nullptr,nullptr,nullptr,1,nullptr) != 42 || tick(40,2) != 42) return 10;
    return 0;
}
