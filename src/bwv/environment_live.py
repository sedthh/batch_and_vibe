"""Read the sky clock and current local precipitation cache. No engine calls."""
import struct
import time

from .environment import Environment, visual_day_period

VISUAL_TIME = 0xBF3380
SKY_THRESHOLDS = 0xFA2694
# Operand references in the supported engine's sky rendering path.
CLOCK_REFERENCE = 0x5E21FF
THRESHOLD_REFERENCE = 0x86A18E
WEATHER_GRID = 0xEDC350
WEATHER_GENERATION = 0xEDC340
GRID_REFERENCE = 0x834F06
GENERATION_REFERENCE = 0x834FB5


def local_precipitation(reader, head):
    """Only use the engine's current cached cell at the creature, never the camera.

    The atmosphere grid has 128x128 cells, each 40 engine units wide. Signed
    rain/snow channels are bytes 1/2; byte 7 identifies the cache generation.
    Reading an old cell cannot refresh it, so stale cells remain unknown.
    """
    game, _, _, creature, _ = head
    if not creature:
        return None, None, None, 'no_creature'
    try:
        if (reader.word(GRID_REFERENCE) != WEATHER_GRID
                or reader.word(GENERATION_REFERENCE) != WEATHER_GENERATION):
            return None, None, None, 'unverified_layout'
        climate = reader.word(game + 0x250534)
        if not climate:
            return None, None, None, 'not_initialized'
        reader.require_type(climate, b'.?AVGClimate@@')
        pos = reader.read(creature + 0x14, 8)
        x, z = struct.unpack('<ii', pos)
        # Fixed-point MapCoords use 65536 per 10 world units. The engine's
        # float-to-int grid indexing truncates toward zero.
        column, row = int(x / 262144), int(z / 262144)
        if not 0 <= column < 128 or not 0 <= row < 128:
            return None, None, None, 'outside_weather_grid'
        grid = reader.word(WEATHER_GRID)
        if not grid:
            return None, None, None, 'not_initialized'
        generation = reader.read(WEATHER_GENERATION, 1)
        address = grid + (row * 128 + column) * 8
        cell = reader.read(address, 8)
        if cell[7:] != generation:
            return None, None, None, 'stale_local_cell'
        if (reader.read(address, 8) != cell
                or reader.read(WEATHER_GENERATION, 1) != generation
                or reader.word(WEATHER_GRID) != grid
                or reader.word(game + 0x250534) != climate
                or reader.read(creature + 0x14, 8) != pos):
            return None, None, None, 'changed_during_read'
        rain, snow = struct.unpack('<bb', cell[1:3])
        weather = ('rain_and_snow' if rain > 0 and snow > 0 else
                   'rain' if rain > 0 else 'snow' if snow > 0 else 'no_rain_or_snow')
        return weather, rain, snow, 'current_creature_cell'
    except (OSError, ValueError):
        # Keep the independent sky-clock result when precipitation is unreadable.
        return None, None, None, 'read_unavailable'


def read_environment(reader, observer):
    reader.ensure_running()
    started = time.monotonic()
    session = reader._session(observer)
    head = reader._head()
    if reader.word(CLOCK_REFERENCE) != VISUAL_TIME or reader.word(THRESHOLD_REFERENCE) != SKY_THRESHOLDS + 12:
        raise ValueError('Unverified sky clock layout')
    hour, = struct.unpack('<f', reader.read(VISUAL_TIME, 4))
    raw = reader.read(SKY_THRESHOLDS, 16)
    thresholds = struct.unpack('<4f', raw)[::-1]
    period = visual_day_period(hour, thresholds)
    weather, rain, snow, weather_validation = local_precipitation(reader, head)
    if reader.read(SKY_THRESHOLDS, 16) != raw or reader._head() != head:
        raise ValueError('Environment changed during observation')
    from .pipe import snapshot
    if reader._session(snapshot(reader.pid)) != session or time.monotonic()-started > .75:
        raise ValueError('Stale environment observation')
    return Environment(period, hour, 'engine_visual_clock', weather, rain, snow, weather_validation)
