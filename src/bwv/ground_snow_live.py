"""Read the snow surface beneath the creature; no terrain scans or engine calls."""
import math
import struct

# GetTerrainMaterial uses snow material 27, otherwise the cell's material table.
# The snow amount is sampled from SnowCover, halved, truncated and compared to 27.
SNOW_MATERIAL = 27
BLOCK_INDEX, BLOCKS = 0xE9C964, 0xE9C564
COUNTRIES, MATERIALS, DEFAULT_MATERIALS = 0xFA75C0, 0xFA74F8, 0xFA7430
SNOW_COVER = 0xEDC344
LAYOUT_REFERENCES = {
    0x804D1D: BLOCK_INDEX, 0x804D35: BLOCKS, 0x804D57: COUNTRIES,
    0x87377A: MATERIALS, 0x87378B: DEFAULT_MATERIALS,
    0x834DD2: SNOW_COVER, 0x86CA89: 0x8D150C, 0x7353AC: 0x999A10,
}


def snow_on_ground(reader, head):
    """Return the engine's snow-surface classification, or None if unreadable.

    Terrain cells are 10 units wide, with 17x17 vertices per 16-cell block.
    SnowCover is a separate 128x128 float grid at 40-unit spacing. Interpolate
    four values at the creature, as the native sampler does. Persistent snow
    terrain and accumulated snow count independently of current precipitation.
    """
    creature = head[3]
    if not creature:
        return None
    try:
        if any(reader.word(at) != value for at, value in LAYOUT_REFERENCES.items()):
            return None
        if (reader.read(0x8D150C, 4) != struct.pack('<f', .025)
                or reader.read(0x999A10, 4) != struct.pack('<f', 27)):
            return None
        checks = []

        def read(at, size):
            data = reader.read(at, size)
            checks.append((at, data))
            return data

        def word(at):
            return struct.unpack('<I', read(at, 4))[0]

        fixed_x, fixed_z = struct.unpack('<ii', read(creature + 0x14, 8))
        cell_x, cell_z = fixed_x // 65536, fixed_z // 65536
        if not (0 <= cell_x < 512 and 0 <= cell_z < 512):
            return None
        index = read(BLOCK_INDEX + (cell_x // 16) * 32 + cell_z // 16, 1)[0]
        if not index:
            return None
        block = word(BLOCKS + index * 4)
        if not block:
            return None
        cell = read(block + ((cell_x % 16) * 17 + cell_z % 16) * 8, 8)
        country = word(COUNTRIES + (cell[6] & 15) * 4)
        if not country:
            return None
        material_index = word(country + cell[4] * 12 + 8)
        if material_index >= 50:
            return None
        material = word(MATERIALS + material_index * 4)
        material = (struct.unpack('<H', read(material, 2))[0] if material
                    else word(DEFAULT_MATERIALS + material_index * 4))
        if material >= 43:
            return None
        snowy = material == SNOW_MATERIAL
        if not snowy:
            cover = word(SNOW_COVER)
            if cover:
                x, z = fixed_x / 262144, fixed_z / 262144
                # The native sampler returns zero outside its interpolation area.
                if 0 <= x < 127 and 0 <= z < 127:
                    column, row = int(x), int(z)
                    top = struct.unpack('<ff', read(cover + (row * 128 + column) * 4, 8))
                    bottom = struct.unpack('<ff', read(cover + ((row + 1) * 128 + column) * 4, 8))
                    if not all(math.isfinite(v) for v in (*top, *bottom)):
                        return None
                    dx, dz = x - column, z - row
                    amount = ((top[0] + (top[1] - top[0]) * dx) * (1 - dz)
                              + (bottom[0] + (bottom[1] - bottom[0]) * dx) * dz)
                    snowy = amount * .5 >= 27
        if any(reader.read(at, len(data)) != data for at, data in checks):
            return None
        return snowy
    except (OSError, ValueError, OverflowError):
        return None
