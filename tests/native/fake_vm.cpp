// Synthetic VM ABI fixture. No game implementation or data.
#include <windows.h>
#include <cstdint>
#include <cstring>
using Callback = int(__cdecl*)(uint32_t);
Callback entered = nullptr, left = nullptr;
bool inside_callback = false;
int current_task = 0, task_reads = 0;
char task_label[96]{};
uint32_t operand_word = 0, operand_type = 0;
extern "C" {
// Original synthetic fixture for the observed raw-word ABI, not game code.
// Even a script float travels as bits in EAX, not as a C float in x87 ST(0).
__declspec(dllexport) uint32_t POP(uint32_t* type) {
    if (type) *type = operand_type;
    return operand_word;
}
__declspec(dllexport) int PUSH(uint32_t word, uint32_t type) {
    operand_word = word; operand_type = type;
    return 719;
}
__declspec(dllexport) int Version() { return 7; }
__declspec(dllexport) int Initialise(int context, void*, void*, Callback enter, Callback leave, int unknown, void*) {
    entered = enter; left = leave;
    return context + unknown;
}
__declspec(dllexport) int LookIn(int context, int flag) {
    if (!entered || !left) return -10; // Observer must register both callbacks.
    current_task = current_task == 17 ? 29 : 17;
    std::strcpy(task_label, current_task == 17 ? "synthetic_story" : "synthetic_creature");
    task_reads = 0; inside_callback = true;
    const int a = entered(3), b = left(3);
    inside_callback = false;
    std::strcpy(task_label, "invalid_after_callback");
#ifndef OMIT_TASK_NUMBER
    if (task_reads != 4) return -12;
#endif
    if ((a != 1003 || b != 2003) && (a != 0 || b != 0)) return -11;
    return context + flag;
}
__declspec(dllexport) int LoadBinary(int context, const char*) { return context; }
__declspec(dllexport) int RestoreState(int context, const char*) { return context; }
__declspec(dllexport) int UnInitialise() { return 123; }
__declspec(dllexport) int Reboot() { return 456; }
__declspec(dllexport) int NumTasks() { return 3; }
#ifndef OMIT_TASK_NUMBER
__declspec(dllexport) int TaskNumber() {
    if (!inside_callback) ExitProcess(80);
    ++task_reads;
    return current_task;
}
__declspec(dllexport) const char* TaskName() {
    if (!inside_callback) ExitProcess(81);
    ++task_reads;
    return task_label;
}
#endif
__declspec(dllexport) int CodeSize() { return 1234; }
__declspec(dllexport) int LoopGlobalVariables(int, int(__cdecl* callback)(const char*, uint32_t, float)) {
    callback("synthetic_hunger", 2, 0.75f);
    return 0;
}
}
