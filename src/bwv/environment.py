"""Compact world context sourced from the engine, never the computer clock."""
from dataclasses import dataclass
import math

DAY_PERIODS = ('night', 'sunrise', 'morning', 'afternoon', 'sunset')


@dataclass(frozen=True)
class Environment:
    time_of_day: str | None = None
    visual_hour: float | None = None
    validation: str = 'unavailable'
    weather_near_creature: str | None = None
    rain_engine_units: int | None = None
    snow_engine_units: int | None = None
    weather_validation: str = 'unavailable'
    snow_on_ground: bool | None = None


def weather_context(weather, snow_on_ground=None):
    """Small independent facts; missing readings do not become false."""
    result = {}
    if isinstance(weather, dict):
        result = {key: value for key, value in weather.items()
                  if key in ('raining', 'snowing', 'snow_on_ground') and type(value) is bool}
    elif weather in ('rain', 'snow', 'rain_and_snow', 'no_rain_or_snow'):
        result = {'raining': weather in ('rain', 'rain_and_snow'),
                  'snowing': weather in ('snow', 'rain_and_snow')}
    if type(snow_on_ground) is bool:
        result['snow_on_ground'] = snow_on_ground
    return result


def visual_day_period(hour, thresholds):
    if (type(hour) not in (int, float) or not math.isfinite(hour) or not 0 <= hour <= 24
            or len(thresholds) != 4 or any(not math.isfinite(v) for v in thresholds)
            or not 0 <= thresholds[0] < thresholds[1] <= thresholds[2] < thresholds[3] <= 12):
        raise ValueError('Invalid engine sky time')
    hour %= 24
    folded = min(hour, 24-hour)
    if folded < thresholds[0]:
        return 'night'
    if folded < thresholds[3]:
        return 'sunrise' if hour < 12 else 'sunset'
    return 'morning' if hour < 12 else 'afternoon'
