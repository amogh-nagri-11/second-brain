"""Turn an answer written to be read into one that sounds right spoken.

The synthesis prompt produces markdown with emoji and ISO timestamps, which is
what we want on the clipboard and in the notification. Read aloud, though, a
date comes out as a bare subtraction ("two thousand twenty six minus eight...")
and an emoji gets announced by name, so the spoken copy gets its own pass.
"""

import re
from datetime import date

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# emoji, dingbats, flags, and the joiners/selectors that glue them together
EMOJI = re.compile(
    "[\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "\U0001F300-\U0001FAFF"    # pictographs, emoticons, transport, supplemental
    "☀-➿"            # misc symbols and dingbats
    "⬀-⯿"            # arrows and geometric shapes
    "️‍]+"           # variation selector, zero-width joiner
)

# 2026-08-22, optionally followed by a time we don't need to read out
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?")

ORDINALS = {1: "first", 2: "second", 3: "third", 5: "fifth", 8: "eighth", 9: "ninth", 12: "twelfth"}


def _ordinal(day: int) -> str:
    if day in ORDINALS:
        return ORDINALS[day]
    if day < 20:
        return f"{day}th"
    tens, unit = divmod(day, 10)
    if unit == 0:
        return {2: "twentieth", 3: "thirtieth"}[tens]
    return f"{'twenty' if tens == 2 else 'thirty'}-{ORDINALS.get(unit, str(unit) + 'th')}"


def _spoken_date(match: re.Match) -> str:
    year, month, day = (int(part) for part in match.group(1, 2, 3))
    try:
        date(year, month, day)
    except ValueError:
        # not a real date -- leave it alone rather than mangling it
        return match.group(0)
    return f"{MONTHS[month - 1]} {_ordinal(day)}, {year}"


def to_speech(text: str) -> str:
    """Rewrite an answer so a text-to-speech engine reads it the way it was meant."""
    if not text:
        return ""

    # models emit non-breaking hyphens and typographic quotes; fold them to ASCII
    # first, otherwise a date written with U+2011 slips past ISO_DATE and gets read
    # out as three numbers
    text = text.replace("\u2011", "-").replace("\u2010", "-").replace("\u2019", "'")

    text = ISO_DATE.sub(_spoken_date, text)
    text = EMOJI.sub(" ", text)

    # markdown markers are read out or land as odd pauses
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)[*_`](.+?)[*_`](?!\w)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    # a leading bullet becomes a spoken "dash"; the line break is pause enough
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)

    # dashes used as punctuation should sound like a pause, not be read aloud
    text = re.sub(r"\s*[—–]\s*", ", ", text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)

    return text.strip()
