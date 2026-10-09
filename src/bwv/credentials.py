"""Read one provider credential without executing dotenv content or logging it."""
import os
from pathlib import Path


def credential_environment(name, path, *, environ=None):
    """Process credentials win; only the configured key is read from the file."""
    if name is None:
        return {}
    env = os.environ if environ is None else environ
    if env.get(name):
        return {name: env[name]}
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(65537)
    except FileNotFoundError:
        return {}
    except OSError:
        raise ValueError('Could not read the private credential file') from None
    try:
        if len(raw) > 65536:
            raise ValueError()
        found = {}
        for line in raw.decode('utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, sep, value = line.partition('=')
            if key.strip() != name:
                continue
            if not sep or name in found:
                raise ValueError()
            value = value.strip()
            if value.startswith(('"', "'")):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 0 or (value[end+1:].strip() and not value[end+1:].lstrip().startswith('#')):
                    raise ValueError()
                value = value[1:end]
            else:
                value = value.split(' #', 1)[0].rstrip()
            # Deliberately no interpolation, shell evaluation or escape expansion.
            if any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError()
            found[name] = value
        return found
    except (UnicodeError, ValueError):
        raise ValueError('Invalid private credential file; use one KEY=value line per credential') from None
