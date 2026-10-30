// Synthetic storage/expiry tests, no game or renderer calls.
#include <algorithm>
#include <cstring>
#include "../../native/action_probe_policy.h"
#include "../../native/thinking_pause.h"
namespace bwv_probe {
inline bool range(uintptr_t,size_t) {return false;}
inline uintptr_t word(uintptr_t) {return 0;}
inline bool type(uintptr_t,const char*) {return false;}
}
#include "../../native/vr_caption.h"
#include <cassert>
int main() {
    using namespace bwv_probe;
    Command command{};
    command.version=ThinkingPause::protocol;assert(action_packet_size(command)==40);
    command.version=caption_protocol;assert(action_packet_size(command)==552);
    command.version=11;assert(action_packet_size(command)==48);
    assert(valid_caption(L"Hello there."));
    assert(!valid_caption(L"\xF8FF"));assert(!valid_caption(L"\xD800"));
    assert(!valid_caption(L"%red"));assert(!valid_caption(L""));
    assert(!valid_caption(L"$C123 bad"));assert(!valid_caption(L"\\C123 bad"));
    assert(styled_caption(L"Bongo: Hello.")==L"$C8443903 Bongo:$C0  Hello.");
    assert(styled_caption(L"[Heard: Hello.]")==L"[Heard: Hello.]");
    assert(styled_caption(L"Plain")==L"Plain");
    std::array<wchar_t,caption_units> long_text;long_text.fill(L'x');
    assert(!valid_caption(long_text.data()));
    caption_buffers[0][0]=L'H';caption_buffers[0][1]=L'i';
    assert(caption_owned(reinterpret_cast<uintptr_t>(&caption_buffers[0][1])));
    assert(!caption_owned(reinterpret_cast<uintptr_t>(long_text.data())));
    caption_epoch=1;caption_creature=20;caption_queued=true;caption_until=10000;
    caption_visible_until=10000;
    caption_tick(2,20,true,1); // changed scene clears only our retained storage
    assert(!caption_queued && caption_buffers[0][0]==0 && caption_buffers[0][1]==0);
    caption_buffers[0][0]=L'X';caption_epoch=2;
    caption_tick(2,20,true,10000);
    assert(caption_buffers[0][0]==0 && long_text[0]==L'x');
}
