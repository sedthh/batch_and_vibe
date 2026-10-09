// Original PE/COFF layout canonicalizer. Contains no game bytes or instructions.
// Keep its byte stream identical to bwv.image_signature.image_signature.
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <vector>
#include <utility>
#include <algorithm>

namespace bwv_compat {
inline constexpr size_t max_image_bytes=128u*1024*1024;
inline constexpr const char* exe_file_sha256="3f58bc02a6c421b4e66ef08716646bf0e1fa55ab292786edb521d7596ed48aba";
inline constexpr const char* exe_layout_sha256="05174c8672b289ae9656013bb68b22ac165a866ef52f8c606b263e4f7ad4a009";

template<class Emit>
bool canonical_pe(const uint8_t* data,size_t size,Emit emit) {
    if(!data || size>max_image_bytes)return false;
    auto fits=[&](uint64_t at,uint64_t n){return at<=size && n<=size-at;};
    auto u16=[&](size_t at){uint16_t v=0;if(fits(at,2))std::memcpy(&v,data+at,2);return v;};
    auto u32=[&](size_t at){uint32_t v=0;if(fits(at,4))std::memcpy(&v,data+at,4);return v;};
    if(!fits(0,64) || u16(0)!=0x5A4D)return false;
    const uint64_t pe=u32(60),optional=pe+24;
    if(!fits(pe,24) || u32(pe)!=0x4550 || u16(pe+4)!=0x14C)return false;
    const size_t count=u16(pe+6),optional_size=u16(pe+20);
    if(!count || count>96 || optional_size<96 || !fits(optional,optional_size)
            || u16(optional)!=0x10B)return false;
    const size_t directories=u32(optional+92);
    if(directories>16 || 96+directories*8>optional_size)return false;
    const uint64_t start=optional+optional_size;
    const uint32_t image_size=u32(optional+56),header_size=u32(optional+60);
    if(!header_size || header_size>image_size || image_size>max_image_bytes
            || start+count*40>header_size || !fits(0,header_size))return false;
    const uint32_t resource=directories>2?u32(optional+112):0;
    const uint32_t resource_size=directories>2?u32(optional+116):0;
    const char prefix[]="BWV-PE-LAYOUT-1";
    std::vector<uint8_t> coff(data+pe+4,data+pe+24);
    std::fill(coff.begin()+4,coff.begin()+16,0);
    std::vector<uint8_t> opt(data+optional,data+optional+optional_size);
    std::fill(opt.begin()+64,opt.begin()+68,0);
    if(directories>4)std::fill(opt.begin()+128,opt.begin()+136,0);
    if(!emit(reinterpret_cast<const uint8_t*>(prefix),sizeof(prefix))
            || !emit(coff.data(),coff.size()) || !emit(opt.data(),opt.size()))return false;
    std::vector<std::pair<uint64_t,uint64_t>> mapped,backed;
    for(size_t i=0;i<count;++i) {
        const size_t at=start+i*40;
        const uint64_t virtual_size=u32(at+8),va=u32(at+12),raw_size=u32(at+16),raw=u32(at+20);
        const uint32_t flags=u32(at+36);
        const auto extent=std::max(virtual_size,raw_size);
        if(!extent || va<header_size || va+extent>image_size)return false;
        for(const auto& old:mapped)if(va<old.second && old.first<va+extent)return false;
        mapped.push_back({va,va+extent});
        if(raw_size) {
            if(raw<header_size || !fits(raw,raw_size))return false;
            for(const auto& old:backed)if(raw<old.second && old.first<raw+raw_size)return false;
            backed.push_back({raw,raw+raw_size});
        }
        for(size_t c=0;c<8;++c)if(data[at+c]>=128)return false;
        const bool ignored=!std::memcmp(data+at,".rsrc\0\0",8)
            && !(flags&0xA0000020) && (flags&0x40000000)
            && resource==va && resource_size && resource_size<=std::min(virtual_size,raw_size);
        if(ignored)for(size_t n=0;n<directories;++n) {
            if(n==2 || n==4)continue;
            const uint64_t rva=u32(optional+96+n*8),length=u32(optional+100+n*8);
            if(rva && rva<va+extent && va<rva+std::max(uint64_t(1),length))return false;
        }
        uint8_t section[40];std::memcpy(section,data+at,40);std::memset(section+20,0,4);
        const uint8_t kind=ignored?'R':'S';
        if(!emit(section,40) || !emit(&kind,1)
                || (!ignored && raw_size && !emit(data+raw,raw_size)))return false;
    }
    return true;
}
}
