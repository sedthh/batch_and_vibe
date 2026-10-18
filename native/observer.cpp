// Original interoperability adapter. Contains no game code or private offsets.
// Only these named VM boundaries are wrapped; all other exports are forwarded.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <sddl.h>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <array>
#include "snapshot_payload.h"
#include "reader_activity.h"
#include "work_timing.h"

#ifdef BWV_ACTION_PROBE
#include "action_probe.h"
#endif

static_assert(sizeof(void*) == 4, "The game requires an x86 bridge");

namespace {
HMODULE self_module = nullptr;
HMODULE original = nullptr;
INIT_ONCE once = INIT_ONCE_STATIC_INIT;
SRWLOCK snapshot_lock = SRWLOCK_INIT;
std::string cached = "{\"protocol\":1,\"mode\":\"observer\",\"ready\":false,\"capabilities\":[]}\n";
uint64_t ticks = 0;
uint64_t epoch = 0;
ULONGLONG last_capture = 0;
int vm_version = 0;
bool initialized = false;
volatile LONG snapshot_invalidated = 1;
bwv_observer::ReaderActivity reader_activity;
bwv_observer::WorkTiming bridge_work, snapshot_work;

using Callback = int(__cdecl*)(uint32_t);
using ErrorCallback = int(__cdecl*)(uint32_t, const char*);
using InitialiseFn = int(__cdecl*)(int, void*, ErrorCallback, Callback, Callback, int, Callback);
using PairFn = int(__cdecl*)(int, int);
using FileFn = int(__cdecl*)(int, const char*);
using EmptyFn = int(__cdecl*)();
using NameFn = const char*(__cdecl*)();
using VariablesCallback = int(__cdecl*)(const char*, uint32_t, float);
using VariablesFn = int(__cdecl*)(int, VariablesCallback);

InitialiseFn initialize_vm = nullptr;
PairFn look_in = nullptr;
FileFn load_binary = nullptr, restore_state = nullptr;
EmptyFn version = nullptr, num_tasks = nullptr, uninitialize_vm = nullptr, reboot_vm = nullptr;
EmptyFn task_number = nullptr;
NameFn task_name = nullptr;
VariablesFn globals = nullptr;
Callback original_enter = nullptr, original_leave = nullptr;

// The engine passes this public VM interface table at initialization. It is not
// recovered by hard-coded memory addresses. Only bounded names are inspected.
struct NativeEntry {
    void* function;
    uint32_t inputs, outputs, reserved;
    char name[128];
};
static_assert(sizeof(NativeEntry) == 144);
std::vector<std::string> native_names;
const char* table_status = "not_initialized";
size_t table_entries_read = 0;
size_t table_unavailable_functions = 0;
bool table_reserved_prefix = false;
std::array<std::string, 464> slot_names;
std::array<bool, 464> traced_slots{};
std::array<uint32_t, 464> slot_inputs{}, slot_outputs{};
struct NativeEvent {
    uint32_t slot; bool leaving; uint64_t tick, sequence; int task;
    bool name_available;
    char task_name[96];
};
std::array<NativeEvent, 32> native_events{};
uint64_t event_count = 0, callbacks_entered = 0, callbacks_left = 0;
bool readable(const void* pointer, size_t size);

void reset_observation() {
    InterlockedExchange(&snapshot_invalidated, 1);
    event_count = callbacks_entered = callbacks_left = 0;
    last_capture = 0;
}

void record_native(uint32_t slot, bool leaving) {
    if (!reader_activity.active(GetTickCount64())) return;
    if (leaving) ++callbacks_left; else ++callbacks_entered;
    if (slot >= traced_slots.size() || !traced_slots[slot]) return;
    const uint64_t sequence = ++event_count;
    // Ask the VM for its current task only inside its native-call boundary.
    // This reads exported metadata; it does not touch the operand stack. The
    // number is diagnostic attribution, never evidence of ownership or success.
    const int task = task_number ? task_number() : 0;
    auto& event = native_events[(sequence - 1) % native_events.size()];
    event = {slot, leaving, ticks, sequence, task, false, {}};
    const char* name = task_name ? task_name() : nullptr;
    // Copy metadata now: a VM-owned pointer may not survive the callback. A
    // conservative unreadable-range result leaves the name unknown.
    if (name && readable(name, sizeof(event.task_name))) {
        size_t n = 0;
        while (n + 1 < sizeof(event.task_name) && name[n]) {
            event.task_name[n] = name[n]; ++n;
        }
        event.name_available = true;
    }
}

int __cdecl observe_enter(uint32_t slot) {
    record_native(slot, false);
    return original_enter ? original_enter(slot) : 0;
}

int __cdecl observe_leave(uint32_t slot) {
    record_native(slot, true);
    return original_leave ? original_leave(slot) : 0;
}

std::string quote(const char* input, size_t limit = 128) {
    std::string out = "\"";
    if (input) for (size_t i = 0; i < limit && input[i]; ++i) {
        unsigned char c = static_cast<unsigned char>(input[i]);
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if (c >= 32 && c < 127) out += c;
        else out += '?';
    }
    return out + '"';
}

bool readable(const void* pointer, size_t size) {
    uintptr_t cursor = reinterpret_cast<uintptr_t>(pointer);
    if (!cursor || size > UINTPTR_MAX - cursor) return false;
    while (size) {
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQuery(reinterpret_cast<void*>(cursor), &region, sizeof(region))) return false;
        if (region.State != MEM_COMMIT || (region.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
        const uintptr_t base = reinterpret_cast<uintptr_t>(region.BaseAddress);
        if (cursor < base || cursor - base >= region.RegionSize) return false;
        const size_t available = region.RegionSize - (cursor - base);
        const size_t consumed = size < available ? size : available;
        cursor += consumed;
        size -= consumed;
    }
    return true;
}

void inspect_table(void* table) {
    native_names.clear();
    for (auto& name : slot_names) name.clear();
    traced_slots.fill(false);
    slot_inputs.fill(0); slot_outputs.fill(0);
    table_entries_read = 0;
    table_unavailable_functions = 0;
    table_reserved_prefix = false;
    table_status = "unsupported_vm";
    if (vm_version != 7) return;
    auto* entries = static_cast<NativeEntry*>(table);
    // Vanilla's public VM interface defines 464 slots. Slot zero can be reserved.
    // Never treat a blank slot as a callable capability or scan beyond the ABI.
    table_status = "abi_limit";
    for (size_t i = 0; i < 464; ++i) {
        if (!readable(entries + i, sizeof(NativeEntry))) { table_status = "unreadable_entry"; break; }
        const auto& entry = entries[i];
        ++table_entries_read;
        if (i == 0 && !entry.name[0]) { table_reserved_prefix = true; continue; }
        size_t n = 0;
        while (n < sizeof(entry.name) && entry.name[n]) {
            if (entry.name[n] < 32 || entry.name[n] > 126) break;
            ++n;
        }
        if (!n || n == sizeof(entry.name) || entry.name[n] != 0) { table_status = "invalid_name"; break; }
        // A named reserved slot can have a null function. It is still interface
        // metadata; availability must never be inferred from its name alone.
        if (!entry.function || !readable(entry.function, 1)) ++table_unavailable_functions;
        native_names.emplace_back(entry.name, n);
        slot_names[i] = native_names.back();
        slot_inputs[i] = entry.inputs; slot_outputs[i] = entry.outputs;
        const auto& name = slot_names[i];
        traced_slots[i] = name.find("CREATURE") != std::string::npos ||
            name.find("DIALOGUE") != std::string::npos || name.find("CAMERA_CONTROL") != std::string::npos;
    }
}

std::vector<std::string> observed_globals;
int __cdecl capture_variable(const char* name, uint32_t type, float value) {
    if (observed_globals.size() >= 64) return 0;
    // Preserve raw bits; object handles must never undergo float conversion.
    uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    observed_globals.push_back("{\"name\":" + quote(name) + ",\"type\":" +
        std::to_string(type) + ",\"value_bits\":" + std::to_string(bits) + "}");
    return 0;
}

void capture(int context) {
    if (GetTickCount64() - last_capture < 500) return;
    bwv_observer::TimedWork measured(snapshot_work);
    last_capture = GetTickCount64();
    observed_globals.clear();
    // Read APIs are called only on the thread that just ran the VM scheduler.
    if (initialized && vm_version == 7 && globals) globals(context, capture_variable);
    std::string data = "{\"protocol\":1,\"mode\":\"observer\",\"pid\":" +
        std::to_string(GetCurrentProcessId()) + ",\"vm_version\":" + std::to_string(vm_version) +
        ",\"session\":" + std::to_string(epoch) + ",\"ticks\":" + std::to_string(ticks) +
        ",\"captured_uptime_ms\":" + std::to_string(last_capture) +
        ",\"ready\":" + (initialized ? "true" : "false") +
        ",\"task_count\":" + std::to_string(initialized && num_tasks ? num_tasks() : 0) +
        ",\"capabilities\":[],\"control\":{\"verified\":false,\"world_ready\":null,"
        "\"loading\":null,\"paused\":null,\"cutscene\":null,\"player_has_control\":null,"
        "\"creature_present\":null,\"script_owns_creature\":null},\"native_table\":{\"status\":" + quote(table_status) +
        ",\"entries_read\":" + std::to_string(table_entries_read) +
        ",\"unavailable_functions\":" + std::to_string(table_unavailable_functions) +
        ",\"reserved_prefix\":" + (table_reserved_prefix ? "true" : "false") + "},\"native_functions\":[";
    for (size_t i = 0; i < native_names.size(); ++i) {
        if (i) data += ',';
        data += quote(native_names[i].c_str());
    }
    data += "],\"native_trace\":{\"entered\":" + std::to_string(callbacks_entered) +
        ",\"left\":" + std::to_string(callbacks_left) + ",\"total_events\":" + std::to_string(event_count) +
        ",\"control_verified\":false,\"events\":[";
    const uint64_t first = event_count > native_events.size() ? event_count - native_events.size() : 0;
    for (uint64_t i = first; i < event_count; ++i) {
        if (i != first) data += ',';
        const auto& event = native_events[i % native_events.size()];
        data += "{\"sequence\":" + std::to_string(event.sequence) + ",\"slot\":" + std::to_string(event.slot) +
            ",\"phase\":" + quote(event.leaving ? "leave" : "enter") + ",\"tick\":" + std::to_string(event.tick) +
            ",\"task_number\":" + (task_number ? std::to_string(event.task) : "null") +
            ",\"task_name\":" + (event.name_available ? quote(event.task_name, sizeof(event.task_name)) : "null") +
            ",\"input_count\":" + std::to_string(slot_inputs[event.slot]) +
            ",\"output_count\":" + std::to_string(slot_outputs[event.slot]) +
            ",\"name\":" + quote(slot_names[event.slot].c_str()) + "}";
    }
    data += "]},\"globals\":[";
    for (size_t i = 0; i < observed_globals.size(); ++i) {
        if (i) data += ',';
        data += observed_globals[i];
    }
    data += "]";
#ifdef BWV_ACTION_PROBE
    data += ",\"action_probe\":" + bwv_probe::json();
#endif
    data += ",\"bridge_performance\":{\"work\":"+bridge_work.json()
        +",\"snapshot\":"+snapshot_work.json()+",\"reader_timeout_ms\":"
        +std::to_string(bwv_observer::ReaderActivity::timeout_ms)+"}}\n";
    bwv_snapshot::bound(data);
    // Never make the game wait for a reader.
    if (TryAcquireSRWLockExclusive(&snapshot_lock)) {
        cached.swap(data);
        InterlockedExchange(&snapshot_invalidated, 0);
        ReleaseSRWLockExclusive(&snapshot_lock);
    }
}

DWORD WINAPI serve(void*) {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return 1;
    DWORD size = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &size);
    std::vector<unsigned char> buffer(size);
    if (!GetTokenInformation(token, TokenUser, buffer.data(), size, &size)) { CloseHandle(token); return 2; }
    CloseHandle(token);
    LPWSTR sid = nullptr;
    if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(buffer.data())->User.Sid, &sid)) return 3;
    std::wstring acl = L"D:P(A;;GR;;;" + std::wstring(sid) + L")";
    LocalFree(sid);
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(acl.c_str(), SDDL_REVISION_1, &descriptor, nullptr)) return 4;
    SECURITY_ATTRIBUTES attributes{sizeof(attributes), descriptor, FALSE};
    std::wstring name = L"\\\\.\\pipe\\bwv-observer-" + std::to_wstring(GetCurrentProcessId());
    // The pipe is outbound only, restricted to this Windows user, and rejects
    // remote clients. No command parser or memory-writing API exists here.
    HANDLE pipe = CreateNamedPipeW(name.c_str(), PIPE_ACCESS_OUTBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, 65536, 0, 2000, &attributes);
    LocalFree(descriptor);
    if (pipe == INVALID_HANDLE_VALUE) return 5;
    for (;;) {
        if (ConnectNamedPipe(pipe, nullptr) || GetLastError() == ERROR_PIPE_CONNECTED) {
            // Mark stale before waking the game thread. Reattachment must not
            // serve the last snapshot from a previous observer session as new.
            const auto now=GetTickCount64();
            const bool resuming=!reader_activity.active(now);
            if(resuming)InterlockedExchange(&snapshot_invalidated,1);
            reader_activity.touch(now);
            if(resuming) {
                const auto deadline=now+1000;
                while(InterlockedCompareExchange(&snapshot_invalidated,0,0)
                        && GetTickCount64()<deadline)Sleep(10);
            }
            AcquireSRWLockShared(&snapshot_lock);
            std::string response = InterlockedCompareExchange(&snapshot_invalidated, 0, 0) ?
                "{\"protocol\":1,\"mode\":\"observer\",\"ready\":false,\"capabilities\":[],\"lifecycle\":\"invalidated\"}\n" : cached;
            ReleaseSRWLockShared(&snapshot_lock);
            DWORD written = 0;
            WriteFile(pipe, response.data(), static_cast<DWORD>(response.size()), &written, nullptr);
            // Blocking clients only hold the IPC worker, never the game thread.
            FlushFileBuffers(pipe);
        }
        DisconnectNamedPipe(pipe);
    }
}

BOOL CALLBACK bootstrap(PINIT_ONCE, PVOID, PVOID*) {
    wchar_t path[32768]{};
    DWORD length = GetModuleFileNameW(nullptr, path, 32768);
    if (!length || length >= 32768) return FALSE;
    std::wstring root(path, length);
    root.resize(root.find_last_of(L"\\/") + 1);
    original = LoadLibraryExW((root + L"_bwv_original_vm.dll").c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!original) return FALSE;
#define RESOLVE(variable, name) variable = reinterpret_cast<decltype(variable)>(GetProcAddress(original, name))
    RESOLVE(initialize_vm, "Initialise"); RESOLVE(look_in, "LookIn");
    RESOLVE(load_binary, "LoadBinary"); RESOLVE(restore_state, "RestoreState");
    RESOLVE(version, "Version"); RESOLVE(num_tasks, "NumTasks");
    RESOLVE(globals, "LoopGlobalVariables"); RESOLVE(uninitialize_vm, "UnInitialise");
    RESOLVE(reboot_vm, "Reboot");
    RESOLVE(task_number, "TaskNumber");
    RESOLVE(task_name, "TaskName");
#undef RESOLVE
    if (!initialize_vm || !look_in || !load_binary || !restore_state || !version || !uninitialize_vm || !reboot_vm) return FALSE;
    vm_version = version();
    // This module's worker is process-lifetime; prevent an early FreeLibrary from
    // unloading the code beneath it. No threads or loader work occurs in DllMain.
    HMODULE pinned = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
        reinterpret_cast<LPCWSTR>(&bootstrap), &pinned)) return FALSE;
    HANDLE worker = CreateThread(nullptr, 0, serve, nullptr, 0, nullptr);
    if (worker) CloseHandle(worker);
#ifdef BWV_ACTION_PROBE
    bwv_probe::initialize();
#endif
    return TRUE;
}

bool ensure() { return InitOnceExecuteOnce(&once, bootstrap, nullptr, nullptr) != FALSE; }
}

extern "C" int __cdecl bwv_Version() { return ensure() ? version() : 0; }
extern "C" int __cdecl bwv_Initialise(int context, void* table, ErrorCallback errors, Callback enter, Callback leave, int unknown, Callback stopped) {
    if (!ensure()) return 0;
#ifdef BWV_ACTION_PROBE
    bwv_probe::end_thinking_for_lifecycle();
#endif
    ++epoch; ticks = 0;
    reset_observation();
    original_enter = enter; original_leave = leave;
    inspect_table(table);
    // Observe the VM's exported callback interface, preserving the engine's
    // original callback arguments, order and return values. No native is invoked.
    int result = initialize_vm(context, table, errors,
        vm_version == 7 ? observe_enter : enter, vm_version == 7 ? observe_leave : leave, unknown, stopped);
    initialized = vm_version == 7;
    return result;
}
extern "C" int __cdecl bwv_LookIn(int context, int flag) {
    if (!ensure()) return 0;
    int result = look_in(context, flag);
    ++ticks;
    const bool observing=reader_activity.active(GetTickCount64());
    if(!observing) {
#ifdef BWV_ACTION_PROBE
        if(initialized && vm_version==7)bwv_probe::tick(epoch,false);
#endif
        return result;
    }
    bwv_observer::TimedWork measured(bridge_work);
#ifdef BWV_ACTION_PROBE
    if (initialized && vm_version == 7) bwv_probe::tick(epoch,true);
#endif
    capture(context);
    return result;
}
extern "C" int __cdecl bwv_LoadBinary(int context, const char* path) {
    if (!ensure()) return 0;
#ifdef BWV_ACTION_PROBE
    bwv_probe::end_thinking_for_lifecycle();
#endif
    ++epoch; ticks = 0;
    reset_observation();
    return load_binary(context, path);
}
extern "C" int __cdecl bwv_RestoreState(int context, const char* path) {
    if (!ensure()) return 0;
#ifdef BWV_ACTION_PROBE
    bwv_probe::end_thinking_for_lifecycle();
#endif
    ++epoch; ticks = 0;
    reset_observation();
    return restore_state(context, path);
}
extern "C" int __cdecl bwv_UnInitialise() {
    if (!ensure()) return 0;
#ifdef BWV_ACTION_PROBE
    bwv_probe::end_thinking_for_lifecycle();
#endif
    initialized = false; ++epoch; native_names.clear();
    reset_observation();
    return uninitialize_vm();
}
extern "C" int __cdecl bwv_Reboot() {
    if (!ensure()) return 0;
#ifdef BWV_ACTION_PROBE
    bwv_probe::end_thinking_for_lifecycle();
#endif
    ++epoch; ticks = 0;
    reset_observation();
    return reboot_vm();
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        self_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}
