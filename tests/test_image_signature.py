"""Invented PE files, not copies or fragments of a game executable."""
import hashlib
import struct
import unittest
from unittest.mock import patch

from bwv.compat import check_image, unsupported_reason
from bwv.image_signature import image_signature
from bwv.pe import PE, PEError


def synthetic_pe():
    data=bytearray(0x600)
    def put(at,fmt,*values):struct.pack_into('<'+fmt,data,at,*values)
    data[:2]=b'MZ';put(60,'I',0x80);data[0x80:0x84]=b'PE\0\0'
    put(0x84,'HHIIIHH',0x14C,2,123,0,0,224,0x102)
    opt=0x98
    put(opt,'H',0x10B);put(opt+16,'I',0x1000);put(opt+28,'I',0x400000)
    put(opt+32,'II',0x1000,0x200);put(opt+56,'II',0x3000,0x200)
    put(opt+68,'HH',2,0);put(opt+92,'I',16)
    put(opt+112,'II',0x2000,0x100)
    for at,name,va,raw,flags in ((0x178,b'.text',0x1000,0x200,0x60000020),
                               (0x1A0,b'.rsrc',0x2000,0x400,0x40000040)):
        data[at:at+8]=name.ljust(8,b'\0')
        put(at+8,'IIIIIIHHI',0x180,va,0x200,raw,0,0,0,0,flags)
    data[0x200:0x400]=b'T'*0x200
    data[0x400:0x600]=b'R'*0x200
    return data


def cases():
    original=synthetic_pe()
    yield 'original',original,True
    for name,at in (('timestamp',0x88),('checksum',0x98+64),('dos_stub',0x40),('resource',0x480)):
        changed=bytearray(original);changed[at]^=1;yield name,changed,True
    yield 'overlay',original+b'SYNTHETIC NON-MAPPED METADATA',True
    changed=bytearray(original);struct.pack_into('<II',changed,0x98+128,len(changed),16)
    yield 'certificate_table',changed+bytes(16),True
    for name,at in (('code',0x220),('image_base',0x98+28),('entry_point',0x98+16),
                    ('permissions',0x178+36),('loader_directory',0x98+96),('virtual_size',0x178+8),
                    ('resource_permissions',0x1A0+39)):
        changed=bytearray(original);changed[at]^=1;yield name,changed,False
    changed=bytearray(original);struct.pack_into('<I',changed,0x1A0+12,0x1000)
    yield 'overlapping_virtual',changed,False
    changed=bytearray(original);struct.pack_into('<I',changed,0x1A0+20,0x200)
    yield 'overlapping_raw',changed,False
    changed=bytearray(original);struct.pack_into('<II',changed,0x98+96+8,0x2050,20)
    yield 'runtime_directory_in_resources',changed,False
    changed=bytearray(original);struct.pack_into('<I',changed,0x98+92,17)
    yield 'invalid_directory_count',changed,False
    yield 'truncated',original[:0x500],False


class ImageSignatureTests(unittest.TestCase):
    def test_metadata_tolerant_matching_rejects_runtime_changes(self):
        expected=image_signature(PE(bytes(synthetic_pe())))
        with patch('bwv.compat.SUPPORTED_LAYOUTS',{'exe':expected}):
            for name,data,allowed in cases():
                with self.subTest(case=name):
                    try:
                        pe=PE(bytes(data))
                        result=check_image(pe,hashlib.sha256(data).hexdigest(),'exe')
                    except PEError:
                        self.assertFalse(allowed);continue
                    self.assertEqual(result['supported'],allowed,result)
                    if allowed:self.assertEqual(result['method'],'matching_layout')

    def test_changed_code_report_identifies_section_not_private_file_path(self):
        expected=image_signature(PE(bytes(synthetic_pe())))
        changed=synthetic_pe();changed[0x220]^=1
        with patch('bwv.compat.SUPPORTED_LAYOUTS',{'exe':expected}):
            result=check_image(PE(bytes(changed)),'unknown','exe')
        self.assertIn('.text',result['different_regions'])
        self.assertIn('fixed addresses were not enabled',result['reason'])
        self.assertIn('.text',unsupported_reason({'compatibility':{'exe':result}}))

    def test_resource_section_cannot_hide_code_or_imports(self):
        base=synthetic_pe();expected=image_signature(PE(bytes(base)))
        for flags in (0x60000040,0xC0000040,0x40000060):
            changed=bytearray(base);struct.pack_into('<I',changed,0x1A0+36,flags)
            report=image_signature(PE(bytes(changed)))
            self.assertFalse(report['sections'][1]['resource_content_ignored'])
            self.assertNotEqual(report['layout_sha256'],expected['layout_sha256'])

    def test_native_and_python_expected_profiles_are_synchronized(self):
        from pathlib import Path
        from bwv.compat import EXE_SHA256
        from bwv.supported_layouts import SUPPORTED_LAYOUTS
        native=(Path(__file__).parents[1]/'native/pe_signature.h').read_text()
        self.assertIn(EXE_SHA256,native)
        self.assertIn(SUPPORTED_LAYOUTS['exe']['layout_sha256'],native)


if __name__=='__main__':unittest.main()
