// Read a synthetic fixture as data only; never load or run the inspected PE.
#include "../../native/pe_signature_windows.h"
#include <fstream>
#include <iterator>
#include <iostream>
int main(int argc,char** argv) {
    if(argc!=2)return 2;
    std::ifstream stream(argv[1],std::ios::binary);
    if(!stream)return 2;
    std::vector<uint8_t> data((std::istreambuf_iterator<char>(stream)),std::istreambuf_iterator<char>());
    const auto result=bwv_compat::digest_image(data.data(),data.size(),true);
    std::cout<<(result.empty()?"invalid":result)<<'\n';
}
