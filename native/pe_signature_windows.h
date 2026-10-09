#pragma once
#include <windows.h>
#include <wincrypt.h>
#include <string>
#include "pe_signature.h"

namespace bwv_compat {
inline std::string digest_image(const uint8_t* data,size_t length,bool normalized) {
    HCRYPTPROV provider=0;HCRYPTHASH hash=0;
    bool ok=CryptAcquireContextW(&provider,nullptr,nullptr,PROV_RSA_AES,CRYPT_VERIFYCONTEXT)
        && CryptCreateHash(provider,CALG_SHA_256,0,0,&hash);
    auto emit=[&](const uint8_t* bytes,size_t n) {
        return n<=max_image_bytes && CryptHashData(hash,bytes,static_cast<DWORD>(n),0);
    };
    if(ok)ok=normalized?canonical_pe(data,length,emit):emit(data,length);
    BYTE digest[32]{};DWORD size=32;
    if(ok)ok=CryptGetHashParam(hash,HP_HASHVAL,digest,&size,0) && size==32;
    if(hash)CryptDestroyHash(hash);
    if(provider)CryptReleaseContext(provider,0);
    std::string result;
    if(ok)for(auto byte:digest) {
        const char* hex="0123456789abcdef";result+=hex[byte>>4];result+=hex[byte&15];
    }
    return result;
}

inline bool read_image(const wchar_t* path,std::vector<uint8_t>& data) {
    HANDLE file=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file==INVALID_HANDLE_VALUE)return false;
    LARGE_INTEGER length{};
    bool ok=GetFileSizeEx(file,&length) && length.QuadPart>0 && length.QuadPart<=max_image_bytes;
    if(ok) {
        data.resize(static_cast<size_t>(length.QuadPart));size_t at=0;
        while(ok && at<data.size()) {
            DWORD read=0;
            ok=ReadFile(file,data.data()+at,static_cast<DWORD>(std::min(size_t(32768),data.size()-at)),&read,nullptr)
                && read>0;
            at+=read;
        }
    }
    CloseHandle(file);return ok;
}
}
