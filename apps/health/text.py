"""
Search normalization, applied to the stored text (Food.search_text) and to the query
alike, so "طعميه" finds "طعمية" and "tameya" finds "ta'meya". Pure, no database.

Arabic: أ إ آ → ا, ة → ه, ى → ي, tashkeel and tatweel removed.
Latin: lowercased, accents removed (é → e), apostrophes and hyphens removed.
Whitespace collapsed.
"""

import re
import unicodedata

ARABIC_MAP = str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ة': 'ه', 'ى': 'ي'})
TASHKEEL = re.compile('[ً-ٰٟۖ-ۭ]')
TATWEEL = 'ـ'
JOINERS = re.compile(r"['’‘`\-‐–]")  # removed: ta'meya = tameya, cous-cous = couscous
WHITESPACE = re.compile(r'\s+')


def _is_arabic(char: str) -> bool:
    return '؀' <= char <= 'ۿ'


def _strip_latin_accents(text: str) -> str:
    """é → e, ç → c. Arabic letters are left alone (their decomposition would change ؤ, ئ...)."""
    out = []
    for char in text:
        if _is_arabic(char):
            out.append(char)
        else:
            out.extend(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
    return ''.join(out)


def normalize(text: str) -> str:
    if not text:
        return ''
    text = TASHKEEL.sub('', text).replace(TATWEEL, '').translate(ARABIC_MAP)
    text = _strip_latin_accents(text).casefold()
    text = JOINERS.sub('', text)
    return WHITESPACE.sub(' ', text).strip()
