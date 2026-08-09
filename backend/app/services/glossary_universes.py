"""Curated franchise / universe detection for glossary scopes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseDef:
    key: str
    display_name: str
    # Lowercase substrings matched against media titles / series names.
    title_markers: tuple[str, ...]


# Small curated pack — matched before LLM classification.
UNIVERSES: tuple[UniverseDef, ...] = (
    UniverseDef(
        key="marvel",
        display_name="Marvel Cinematic Universe",
        title_markers=(
            "marvel",
            "avengers",
            "iron man",
            "captain america",
            "thor",
            "guardians of the galaxy",
            "spider-man",
            "spiderman",
            "doctor strange",
            "black panther",
            "black widow",
            "ant-man",
            "ant man",
            "captain marvel",
            "ms. marvel",
            "ms marvel",
            "wandavision",
            "wanda vision",
            "loki",
            "falcon and the winter soldier",
            "hawkeye",
            "moon knight",
            "she-hulk",
            "secret invasion",
            "echo",
            "agatha",
            "daredevil",
            "punisher",
            "jessica jones",
            "luke cage",
            "iron fist",
            "the defenders",
            "what if",
            "deadpool",
            "wolverine",
            "x-men",
            "fantastic four",
            "eternals",
            "shang-chi",
            "multiverse of madness",
            "civil war",
            "infinity war",
            "endgame",
            "age of ultron",
        ),
    ),
    UniverseDef(
        key="dc",
        display_name="DC Universe",
        title_markers=(
            "batman",
            "superman",
            "wonder woman",
            "justice league",
            "aquaman",
            "flash",
            "the flash",
            "green lantern",
            "cyborg",
            "shazam",
            "black adam",
            "joker",
            "harley quinn",
            "suicide squad",
            "birds of prey",
            "peacemaker",
            "doom patrol",
            "titans",
            "swamp thing",
            "constantine",
            "watchmen",
            "man of steel",
            "batman v superman",
            "zack snyder",
            "arrow",
            "supergirl",
            "legends of tomorrow",
            "batwoman",
            "stargirl",
            "blue beetle",
            "the batman",
        ),
    ),
    UniverseDef(
        key="star-wars",
        display_name="Star Wars",
        title_markers=(
            "star wars",
            "the mandalorian",
            "mandalorian",
            "andor",
            "obi-wan",
            "ahsoka",
            "the book of boba fett",
            "skeleton crew",
            "the acolyte",
            "rogue one",
            "solo: a star wars",
            "clone wars",
            "rebels",
            "the bad batch",
            "tales of the jedi",
            "visions",
        ),
    ),
    UniverseDef(
        key="harry-potter",
        display_name="Wizarding World",
        title_markers=(
            "harry potter",
            "fantastic beasts",
            "hogwarts",
            "wizarding world",
        ),
    ),
    UniverseDef(
        key="lord-of-the-rings",
        display_name="Middle-earth",
        title_markers=(
            "lord of the rings",
            "the hobbit",
            "rings of power",
            "middle-earth",
        ),
    ),
    UniverseDef(
        key="star-trek",
        display_name="Star Trek",
        title_markers=(
            "star trek",
            "discovery",
            "picard",
            "strange new worlds",
            "lower decks",
            "prodigy",
        ),
    ),
)


def match_universe(media_title: str | None) -> UniverseDef | None:
    if not media_title:
        return None
    haystack = media_title.casefold()
    best: UniverseDef | None = None
    best_len = 0
    for universe in UNIVERSES:
        for marker in universe.title_markers:
            if marker in haystack and len(marker) > best_len:
                best = universe
                best_len = len(marker)
    return best


def universe_keys() -> list[str]:
    return [u.key for u in UNIVERSES]
