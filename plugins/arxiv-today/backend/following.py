"""Match followed publication names locally, without conflating first initials."""

import unicodedata

from backend.plugins import scholarly


def _name_key(name: str) -> tuple[str, str]:
    def normalize(part: str) -> str:
        return " ".join(
            "".join(c for c in unicodedata.normalize("NFKD", part.casefold()) if not unicodedata.combining(c)).split()
        )

    family, given = scholarly.parse_display_name(name)
    return normalize(family), normalize(given)


def followed_names(authors: list[str], followed: list[str]) -> list[str]:
    keys = {_name_key(n) for n in followed if n.strip()}
    return list(dict.fromkeys(n for n in authors if _name_key(n) in keys))
