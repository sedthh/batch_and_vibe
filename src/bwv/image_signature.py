"""Hash a PE's loader contract and mapped sections, without executing it.

Only isolated read-only resource contents and non-runtime file metadata are
excluded. Code, initialized data, imports, relocations and memory layout remain
bound. See Microsoft's PE/COFF specification; this is not signature scanning or
a way to relocate this mod's fixed engine addresses.
"""
import hashlib
import struct

from .pe import PEError

PREFIX = b'BWV-PE-LAYOUT-1\0'
MAX_IMAGE_BYTES = 128 * 1024 * 1024


def image_signature(pe):
    if len(pe.data)>MAX_IMAGE_BYTES or pe.machine!=0x14C or pe.bits!=32:
        raise PEError('Expected bounded x86 PE32 image')
    count=pe.u16(pe.header+6)
    optional_size=pe.u16(pe.header+20)
    if optional_size<96 or not 0<count<=96:
        raise PEError('Invalid loader headers')
    image_size=pe.u32(pe.optional+56)
    header_size=pe.u32(pe.optional+60)
    start=pe.optional+optional_size
    if not 0<header_size<=image_size<=MAX_IMAGE_BYTES or start+count*40>header_size:
        raise PEError('Invalid image or header bounds')
    pe.take(0,header_size)
    coff=bytearray(pe.take(pe.header+4,20))
    coff[4:16]=bytes(12)  # COFF timestamp and non-runtime symbol table pointers.
    optional=bytearray(pe.take(pe.optional,optional_size))
    optional[64:68]=bytes(4)  # File checksum.
    if pe.directory_count>4:optional[128:136]=bytes(8)  # Certificate file offset/size.
    header=PREFIX+coff+optional
    digest=hashlib.sha256(header)
    resource=pe.directory_rva(2)
    resource_size=pe.u32(pe.directory+2*8+4) if pe.directory_count>2 else 0
    sections=[];memory_ranges=[];file_ranges=[]
    for i in range(count):
        at=start+i*40
        raw_header=bytearray(pe.take(at,40))
        name=bytes(raw_header[:8]).rstrip(b'\0').decode('ascii')
        virtual_size,va,raw_size,raw=struct.unpack_from('<IIII',raw_header,8)
        flags=pe.u32(at+36)
        extent=max(virtual_size,raw_size)
        if not extent or va<header_size or va+extent>image_size:
            raise PEError('Section outside mapped image')
        if any(va<end and begin<va+extent for begin,end in memory_ranges):
            raise PEError('Overlapping mapped sections')
        memory_ranges.append((va,va+extent))
        data=pe.take(raw,raw_size) if raw_size else b''
        if raw_size:
            if raw<header_size or any(raw<end and begin<raw+raw_size for begin,end in file_ranges):
                raise PEError('Overlapping file sections')
            file_ranges.append((raw,raw+raw_size))
        # Only a dedicated non-executable, non-writable resource section qualifies.
        ignored=(name=='.rsrc' and not flags&0xA0000020 and bool(flags&0x40000000)
                 and resource==va and 0<resource_size<=min(virtual_size,raw_size))
        if ignored:
            for n in range(pe.directory_count):
                if n in (2,4):continue
                rva=pe.directory_rva(n);size=pe.u32(pe.directory+n*8+4)
                if rva and rva<va+extent and va<rva+max(1,size):
                    raise PEError('Resource section contains a runtime directory')
        raw_header[20:24]=bytes(4)  # File placement is not a virtual address.
        digest.update(raw_header)
        digest.update(b'R' if ignored else b'S')
        if not ignored:digest.update(data)
        sections.append({'name':name,'rva':va,'virtual_size':virtual_size,
                         'raw_size':raw_size,'flags':flags,'resource_content_ignored':ignored,
                         'sha256':None if ignored else hashlib.sha256(data).hexdigest()})
    return {'layout_sha256':digest.hexdigest(),'headers_sha256':hashlib.sha256(header).hexdigest(),
            'sections':sections}
