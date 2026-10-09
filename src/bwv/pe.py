"""Bounded PE metadata inspection; never loads or executes an inspected file."""

from __future__ import annotations

import struct
from pathlib import Path


class PEError(ValueError):
    pass


class PE:
    def __init__(self, data: bytes):
        self.data = data
        if data[:2] != b"MZ":
            raise PEError("Missing DOS header")
        self.header = self.u32(60)
        if self.take(self.header, 4) != b"PE\0\0":
            raise PEError("Missing PE header")
        self.machine = self.u16(self.header + 4)
        count = self.u16(self.header + 6)
        if not 0 < count <= 96:
            raise PEError("Invalid section count")
        optional_size = self.u16(self.header + 20)
        self.optional = self.header + 24
        self.take(self.optional, optional_size)
        magic = self.u16(self.optional)
        if magic not in (0x10B, 0x20B):
            raise PEError("Unsupported optional header")
        self.bits = 32 if magic == 0x10B else 64
        self.directory = self.optional + (96 if self.bits == 32 else 112)
        self.directory_count = self.u32(self.directory - 4)
        if self.directory_count > 16 or self.directory + self.directory_count * 8 > self.optional + optional_size:
            raise PEError("Invalid data directories")
        start = self.optional + optional_size
        self.sections = []
        for i in range(count):
            p = start + i * 40
            self.take(p, 40)
            va, raw_size, raw = self.u32(p + 12), self.u32(p + 16), self.u32(p + 20)
            self.sections.append((va, raw_size, raw))

    @classmethod
    def read(cls, path: Path) -> PE:
        if path.stat().st_size > 128 * 1024 * 1024:
            raise PEError("PE file exceeds inspection limit")
        return cls(path.read_bytes())

    def take(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise PEError("Truncated or malformed PE file")
        return self.data[offset:offset + size]

    def u16(self, offset: int) -> int:
        return struct.unpack("<H", self.take(offset, 2))[0]

    def u32(self, offset: int) -> int:
        return struct.unpack("<I", self.take(offset, 4))[0]

    def rva(self, address: int, size: int = 1) -> int:
        for va, raw_size, raw in self.sections:
            if va <= address and address + size <= va + raw_size:
                p = raw + address - va
                self.take(p, size)
                return p
        raise PEError("RVA does not point to file-backed section data")

    def directory_rva(self, number: int) -> int:
        return self.u32(self.directory + number * 8) if number < self.directory_count else 0

    def string(self, address: int) -> str:
        out = bytearray()
        for i in range(512):
            value = self.data[self.rva(address + i)]
            if value == 0:
                return out.decode("ascii")
            out.append(value)
        raise PEError("Unterminated PE name")

    @property
    def managed(self) -> bool:
        return bool(self.directory_rva(14))

    def exports(self) -> list[str]:
        address = self.directory_rva(0)
        if not address:
            return []
        p = self.rva(address, 40)
        count = self.u32(p + 24)
        if count > 65536:
            raise PEError("Too many export names")
        table = self.rva(self.u32(p + 32), count * 4)
        return [self.string(self.u32(table + i * 4)) for i in range(count)]

