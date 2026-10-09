"""One editable general prompt; technical bridge contracts live in code.

Bundled settings.default.json is the single source for the default prose.
"""
from dataclasses import dataclass
from importlib.resources import files
import json
from .prompt_migration import migrate_prompt_sections


_DEFAULT_GENERAL = json.loads(files('bwv').joinpath('settings.default.json').read_text(
    encoding='utf-8'))['prompts']['general']


@dataclass(frozen=True)
class PromptSettings:
    general: str = _DEFAULT_GENERAL

    def __post_init__(self):
        if (not isinstance(self.general, str) or not self.general.strip()
                or len(self.general) > 20000
                or any(ord(c) < 32 and c not in '\n\r\t' for c in self.general)):
            raise ValueError('General prompt must contain 1 to 20000 characters without control characters')
        try:
            self.general.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('General prompt contains invalid Unicode; remove or retype the damaged character') from None

    @classmethod
    def parse(cls, data):
        return cls(**migrate_prompt_sections(data, _DEFAULT_GENERAL))


DEFAULT_PROMPTS=PromptSettings()
