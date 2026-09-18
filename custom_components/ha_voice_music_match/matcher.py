"""Match a misheard music request against the library, on spelling and sound.

No Home Assistant imports. Filler words, "X by Y" and the sound key assume English.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
import re
import unicodedata

ARTIST = "artist"
ALBUM = "album"
TRACK = "track"
PLAYLIST = "playlist"
MEDIA_TYPES = (ARTIST, ALBUM, TRACK, PLAYLIST)

# An artist or album within this margin of the best track wins.
TIE_MARGIN = 0.03

# Default thresholds: play at ACT, ask "did you mean" at ASK. Tuned on Piper
# speech transcribed by faster-whisper against a 2,333-item library.
ACT = 0.70
ASK = 0.62

# A winner this far ahead of the next differently-named item plays; a closer
# one asks instead. The bigger the library, the more often two unrelated names
# both score above ACT, so this does the work a fixed threshold cannot. On the
# calibration corpus 0.04 cost no right plays at 2,400 items; a library in the
# tens of thousands wants more, around 0.08.
MARGIN = 0.04

# Above PREFILTER_MIN_ITEMS, only the PREFILTER_KEEP items sharing the most
# three-letter chunks with the request are scored.
PREFILTER_MIN_ITEMS = 5_000
PREFILTER_KEEP = 1_000

SOUND_WEIGHT = 0.5
SOUND_FULL_WEIGHT_LEN = 5

# Words people put around a name that are not part of it.
_LEADING_FILLER = re.compile(
    r"^(?:some|a bit of|a little|my|the|music by|songs by|something by|anything by)\s+"
)
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


class Band(StrEnum):
    """What to do with a match."""

    ACT = "act"
    ASK = "ask"
    NONE = "none"


def normalise(text: str) -> str:
    """Lower-case words only: accents, brackets, "feat." and punctuation go."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = _BRACKETS.sub("", text)
    text = _FEAT.sub("", text)
    text = text.replace("&", " and ").replace("'", "")
    text = _NON_ALNUM.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


def strip_filler(text: str) -> str:
    """Drop leading filler ("some", "music by") from a normalised request."""
    previous = None
    while previous != text:
        previous = text
        text = _LEADING_FILLER.sub("", text)
    return text


_PHONETIC_RULES = (
    ("ph", "f"),
    ("ck", "k"),
    ("sch", "sk"),
    ("tch", "ch"),
    ("wh", "w"),
    ("kn", "n"),
    ("q", "k"),
    ("x", "ks"),
    ("z", "s"),
    ("c", "k"),
    ("v", "f"),
    ("d", "t"),
    ("b", "p"),
    ("g", "k"),
)


def phonetic(compact: str) -> str:
    """Fold a spaceless name to a rough sound key: consonant classes, no vowels."""
    if not compact:
        return ""
    key = compact
    for src, dst in _PHONETIC_RULES:
        key = key.replace(src, dst)
    first, rest = key[0], re.sub(r"[aeiouyhw]", "", key[1:])
    key = first + rest
    return re.sub(r"(.)\1+", r"\1", key)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    return matcher.ratio()


@dataclass(frozen=True)
class _Keys:
    norm: str
    compact: str
    sound: str

    @classmethod
    def of(cls, text: str) -> _Keys:
        norm = normalise(text)
        compact = norm.replace(" ", "")
        return cls(norm, compact, phonetic(compact))


def similarity(heard: _Keys, name: _Keys) -> float:
    """Score two names 0..1 on spelling and sound."""
    if heard.compact == name.compact:
        return 1.0
    spelling = _ratio(heard.compact, name.compact)
    sound = _ratio(heard.sound, name.sound)
    # Sound only lifts a score. Short keys match almost anything, so they count for less.
    weight = SOUND_WEIGHT * min(1.0, min(len(heard.sound), len(name.sound)) / SOUND_FULL_WEIGHT_LEN)
    return max(spelling, (1 - weight) * spelling + weight * sound)


@dataclass(frozen=True)
class LibraryItem:
    """One playable thing in the library."""

    media_type: str
    name: str
    uri: str
    artists: tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    """The chosen item, how sure the match is, and what came second.

    The runner-up is the closest item under a different name. A library holds
    the same name many times over (a track on three albums, an artist and their
    self-titled album), and those are the same answer, not a rival one.
    """

    item: LibraryItem
    score: float
    band: Band
    runner_up: LibraryItem | None = None
    runner_up_score: float = 0.0


@dataclass
class _Entry:
    item: LibraryItem
    keys: _Keys
    artist_keys: tuple[_Keys, ...] = ()


def band_for(score: float, act: float = ACT, ask: float = ASK) -> Band:
    """Map a score to what should happen."""
    if score >= act:
        return Band.ACT
    if score >= ask:
        return Band.ASK
    return Band.NONE


def _grams(keys: _Keys) -> set[str]:
    """Three-letter chunks of the spelling and sound keys, kept apart by prefix."""
    grams: set[str] = set()
    for prefix, text in (("c", keys.compact), ("s", keys.sound)):
        padded = f" {text} "
        grams.update(prefix + padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


class Matcher:
    """Library names prepared once, matched per request."""

    def __init__(self, items: list[LibraryItem]) -> None:
        """Precompute keys for every item, and the chunk index for big libraries."""
        self._entries = [
            _Entry(
                item,
                _Keys.of(item.name),
                tuple(_Keys.of(artist) for artist in item.artists),
            )
            for item in items
            if item.media_type in MEDIA_TYPES and normalise(item.name)
        ]
        self._index: dict[str, list[int]] | None = None
        self._gram_counts: list[int] = []
        if len(self._entries) > PREFILTER_MIN_ITEMS:
            self._build_index()

    def _build_index(self) -> None:
        index: dict[str, list[int]] = defaultdict(list)
        for position, entry in enumerate(self._entries):
            grams = _grams(entry.keys)
            self._gram_counts.append(len(grams))
            for gram in grams:
                index[gram].append(position)
        self._index = dict(index)

    def _candidates(self, queries: list[_Keys], media_type: str | None) -> list[_Entry]:
        """The entries worth scoring: all of them, or the closest by shared chunks."""
        if self._index is None:
            return self._entries
        grams = set().union(*(_grams(keys) for keys in queries))
        shared: dict[int, int] = defaultdict(int)
        for gram in grams:
            for position in self._index.get(gram, ()):
                shared[position] += 1
        ranked = sorted(
            (
                position
                for position in shared
                if not media_type or self._entries[position].item.media_type == media_type
            ),
            key=lambda position: shared[position] / (self._gram_counts[position] + len(grams)),
            reverse=True,
        )
        return [self._entries[position] for position in ranked[:PREFILTER_KEEP]]

    def __len__(self) -> int:
        """Number of matchable items."""
        return len(self._entries)

    def match(
        self,
        heard: str,
        media_type: str | None = None,
        act: float = ACT,
        ask: float = ASK,
        margin: float = MARGIN,
    ) -> Match | None:
        """Return the best library item for what was heard, or None if there are no candidates.

        Blocking: call it from an executor.
        """
        query = strip_filler(normalise(heard))
        if not query:
            return None
        whole = _Keys.of(query)

        # "X by Y": also try X as the title and Y as the artist.
        title_artist: tuple[_Keys, _Keys] | None = None
        if " by " in query:
            title, artist = query.rsplit(" by ", 1)
            if title and artist:
                title_artist = (_Keys.of(title), _Keys.of(artist))

        queries = [whole, title_artist[0]] if title_artist else [whole]
        scored: list[tuple[float, _Entry]] = []
        for entry in self._candidates(queries, media_type):
            if media_type and entry.item.media_type != media_type:
                continue
            score = similarity(whole, entry.keys)
            if title_artist and entry.artist_keys:
                title_score = similarity(title_artist[0], entry.keys)
                artist_score = max(similarity(title_artist[1], a) for a in entry.artist_keys)
                score = max(score, 0.6 * title_score + 0.4 * artist_score)
            scored.append((score, entry))

        if not scored:
            return None
        scored.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best = scored[0]

        if best.item.media_type == TRACK:
            for score, entry in scored[1:]:
                if best_score - score > TIE_MARGIN:
                    break
                if entry.item.media_type in (ARTIST, ALBUM):
                    best_score, best = score, entry
                    break

        runner = next(((s, e) for s, e in scored if e.keys.norm != best.keys.norm), None)
        band = band_for(best_score, act, ask)
        if band is Band.ACT and runner and best_score < 1.0 and best_score - runner[0] < margin:
            # Two names this close is a coin toss. Ask rather than guess.
            band = Band.ASK
        return Match(
            item=best.item,
            score=round(best_score, 3),
            band=band,
            runner_up=runner[1].item if runner else None,
            runner_up_score=round(runner[0], 3) if runner else 0.0,
        )
