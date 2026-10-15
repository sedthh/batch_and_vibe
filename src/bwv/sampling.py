"""Bounded detailed-sensor reuse, independent of fast control/event sampling."""


class DetailRefresh:
    def __init__(self, interval=3.0):
        self.interval=interval
        self.requested=0
        self.completed=0
        self.clear()

    def clear(self):
        self.identity=None
        self.value=None
        self.sampled_at=None
        self.duration_ms=None
        self.tick=None

    def request(self):
        # Concurrent requests share one scan; a request arriving during a scan
        # is acknowledged only by a scan that started after that request.
        if self.requested==self.completed:self.requested+=1
        return self.requested

    def due(self, identity, now, tick=None):
        rolled_back=(type(tick) is int and type(self.tick) is int and tick<self.tick)
        self.tick=tick
        return (self.value is None or identity!=self.identity or rolled_back
            or self.requested!=self.completed or self.sampled_at is None
            or now<self.sampled_at or now-self.sampled_at>=self.interval)

    def store(self, identity, value, sampled_at, duration_ms, revision):
        self.identity=identity
        self.value=value
        self.sampled_at=sampled_at
        self.duration_ms=duration_ms
        self.completed=revision

    def diagnostics(self, now):
        return {'interval_seconds':self.interval,'revision':self.completed,
            'observed_at':self.sampled_at,
            'age_seconds':None if self.sampled_at is None else round(max(0,now-self.sampled_at),3),
            'duration_ms':self.duration_ms}
