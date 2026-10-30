// Optional captions through the game's own help-text renderer. No render hooks.
#pragma once
#include <array>
#include <cwchar>
#include <string>

namespace bwv_probe {
constexpr uint32_t caption_protocol=15;
static_assert(caption_protocol!=ThinkingPause::protocol,"Caption and thinking messages must stay distinct");
constexpr size_t caption_units=256;
inline size_t action_packet_size(const Command& command) {
    return command.version==caption_protocol?sizeof(Command)+caption_units*2:
        command.protocol()==11?48:sizeof(Command);
}
std::array<wchar_t,caption_units> caption_pending{};
// HelpText borrows its strings. Never free a buffer while the game can retain it.
std::array<std::array<wchar_t,caption_units+32>,7> caption_buffers{};
uint64_t caption_epoch=0,caption_until=0,caption_delivered=0;
uint64_t caption_visible_until=0;
uintptr_t caption_creature=0;
bool caption_queued=false;
bool caption_busy=true;
bool caption_notice=false;
const char* caption_status="idle";

inline bool valid_caption(const wchar_t* text) {
    if(!text[0])return false;
    for(size_t i=0;i<caption_units;++i) {
        const auto c=static_cast<uint16_t>(text[i]);
        if(!c)return true;
        // Keep engine markup and malformed UTF-16 out of the legacy renderer.
        if(c<32 || (c>=0x7F && c<=0x9F) || (c>=0xD800 && c<=0xF8FF)
                || c>=0xFFFE || c=='<' || c=='>' || c=='%' || c=='$' || c=='\\')return false;
    }
    return false;
}

inline std::wstring styled_caption(const wchar_t* text) {
    std::wstring result(text);
    const auto colon=result.find(L':');
    if(result.empty() || result[0]==L'[' || colon==std::wstring::npos || colon>48)return result;
    // Verified HelpText $C decimal BBGGRR command; $C0 restores the speaker
    // default. Only this authored formatter may introduce engine commands.
    return L"$C8443903 "+result.substr(0,colon+1)+L"$C0 "+result.substr(colon+1);
}

inline bool caption_owned(uintptr_t pointer) {
    for(const auto& buffer:caption_buffers) {
        const auto start=reinterpret_cast<uintptr_t>(buffer.data());
        if(pointer>=start && pointer<start+sizeof(buffer))return true;
    }
    return false;
}

inline bool caption_entry(uintptr_t address,const unsigned char* prefix,size_t size) {
    return range(address,size) && !std::memcmp(reinterpret_cast<void*>(address),prefix,size);
}

inline void caption_tick(uint64_t epoch,uintptr_t creature,bool allowed,uint64_t now) {
    caption_busy=true;
    const bool changed=epoch!=caption_epoch || creature!=caption_creature || !allowed;
    if(caption_queued && (changed || now>caption_until)) {
        caption_queued=false;caption_status="context_changed";
    }
    const bool expired=changed || now>=caption_visible_until;
    // Only our storage is touched when native dialogue takes ownership. The game
    // may retain pointers into it, so erase all characters and keep it allocated.
    if(expired)for(auto& buffer:caption_buffers)buffer.fill(0);
    const auto game=word(0xD0195C),help=word(game+0x25005C),text=word(help+0x14);
    if(!game || !range(help,0x4608) || !type(text,".?AVHelpText@@") || !range(text,0xBC)) {
        caption_queued=false;caption_status="help_unavailable";return;
    }
    // Reset retained history only when every pointer belongs to us. Never reset
    // someone else's help text, including a story line arriving between pages.
    bool owned=false,foreign=false;
    for(size_t i=0;i<6;++i) {
        const auto pointer=word(text+0x34+i*16);
        if(pointer) {if(caption_owned(pointer))owned=true;else foreign=true;}
    }
    const auto current=word(text+8);
    // str can be the engine's parsed working string, rather than the borrowed
    // source. Queue ownership decides whether resetting that display is ours.
    if(current && !caption_owned(current) && !owned)foreign=true;
    const bool dialogue=word(help+0x45CC) || word(help+0x57C) || word(help+0x580) || word(help+0x45E8);
    if(owned && expired && !foreign && !dialogue) {
        const unsigned char reset_prefix[]={0x8A,0x44,0x24,0x04,0x53,0x33,0xDB};
        if(!caption_entry(0x5CB020,reset_prefix,sizeof(reset_prefix))) {
            caption_queued=false;caption_status="text_entry_changed";return;
        }
        using Reset=void(__thiscall*)(void*,bool);
        reinterpret_cast<Reset>(0x5CB020)(reinterpret_cast<void*>(text),true);
        for(auto& buffer:caption_buffers)buffer.fill(0);
        owned=false;caption_status="expired";
    }
    // Dialogue, click-to-continue, widescreen, and native help always win.
    if(dialogue) {
        caption_status="native_dialogue";return;
    }
    if(foreign || owned) {
        caption_status="native_help_busy";return;
    }
    caption_busy=!allowed;
    if(!caption_queued || !allowed)return;
    // The exact 1.42 function is verified against disk by fingerprint(). Refuse
    // an entry detoured by another mod instead of guessing its calling convention.
    const unsigned char prefix[]={0x8B,0x81,0x94,0,0,0,0x40,0x99};
    if(!caption_entry(0x5CCED0,prefix,sizeof(prefix))) {
        caption_queued=false;caption_status="text_entry_changed";return;
    }
    auto& buffer=caption_buffers[caption_delivered%caption_buffers.size()];
    // The queue is empty; avoid the string currently retained by HelpText too.
    if(word(text+8)==reinterpret_cast<uintptr_t>(buffer.data())) {
        caption_status="text_retained";return;
    }
    const unsigned char color_prefix[]={0x8B,0x44,0x24,0x04,0x85,0xC0};
    const auto formatted=caption_entry(0x5CB400,color_prefix,sizeof(color_prefix))?
        styled_caption(caption_pending.data()):std::wstring(caption_pending.data());
    buffer.fill(0);std::copy(formatted.begin(),formatted.end(),buffer.begin());
    using Append=void(__thiscall*)(void*,const wchar_t*,float,uint32_t);
    reinterpret_cast<Append>(0x5CCED0)(reinterpret_cast<void*>(text),buffer.data(),0.0f,0);
    caption_visible_until=now+(caption_notice?4000:10000);
    ++caption_delivered;caption_queued=false;caption_status="submitted_to_game";
}
}
