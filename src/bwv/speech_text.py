"""Text-to-speech input is creature dialogue, without UI notices or emoji cues."""
import re
from .expressions import display_speech


_NOTICE=re.compile(r'\[(?:ERROR\s*:|Voice\b|Model error\s*:|Heard\s*:|'
    r'[^\[\]\r\n]{1,80} is obeying your command\b)[^\[\]]*\]',re.IGNORECASE)


def spoken_text(text):
    text=display_speech(text).strip()
    # Standalone bracketed banners are presentation, even in older stored
    # messages without source metadata. Keep normal dialogue around notices.
    text=re.sub(r'^\s*\[[^\[\]\r\n]*\]\s*$', '',text,flags=re.MULTILINE)
    text=_NOTICE.sub(' ',text)
    return ' '.join(text.split())
