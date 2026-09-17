"""Match a misheard music request against the library.

Pure Python with no Home Assistant imports, so it can be tested and tuned on
its own. Names are compared two ways and the better score wins:

- spelling: the normalised name with spaces removed, so "gangsta grass" and
  "Gangstagrass" line up;
- sound: a phonetic key that folds letters people and speech-to-text confuse
  (c/k/q, s/z, ph/f, dropped vowels), so "Gaza Grass" still lands near it.

The cleanup rules ("the", "some", "X by Y") and the sound key follow English.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
import re
import unicodedata

ARTIST = "artist"
ALBUM = "album"
TRACK = "track"
PLAYLIST = "playlist"
MEDIA_TYPES = (ARTIST, ALBUM, TRACK, PLAYLIST)

# A track this close to the best artist/album score loses the tie to it
# (user decision: artists and albums win ties against songs).
TIE_MARGIN = 0.03

# Default thresholds; users can change both in the integration's options.
# Scores at or above ACT play straight away. Between ASK and ACT is the "very
# low confidence" band that asks "did you mean". Below ASK the request is
# handed to Music Assistant's own search unchanged.
#
# Calibrated on a 2,333-item library: Piper said "Play <name>" for every
# artist and album plus 120 songs, and Whisper (faster-whisper small-int8)
# transcribed it. 71% of requests played the right item, 1% the wrong one,
# 13% asked, 15% fell through. For 31 names not in the library (62
# transcripts), 12 would play something else (the reply names it), 28 ask,
# 22 fall through.
ACT = 0.70
ASK = 0.62

# Scoring every item takes about 25 microseconds, so 100,000 items take over
# two seconds. Above PREFILTER_MIN_ITEMS, only the PREFILTER_KEEP items that
# share the most three-letter chunks with the request are scored.
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
    # Sound can only lift a score, never lower it. A short key ("kr" for both
    # "the car" and "Cure") matches almost anything, so its weight shrinks
    # below SOUND_FULL_WEIGHT_LEN characters.
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
    """The chosen item, how sure the match is, and what came second."""

    item: LibraryItem
    score: float
    band: Band
    runner_up: LibraryItem | None = None
    runner_up_score: float = 0.0


@dataclass
class _Entry:
    item: LibraryItem
    keys: _Keys
    artist_keys: tuple[_Keys, ...] = field(default_factory=tuple)


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
    ) -> Match | None:
        """Return the best library item for what was heard.

        None when the library is empty, or when it is big enough to pre-filter
        and no name shares a three-letter chunk with the request.

        Slow on big libraries: call it from a worker thread, not the event loop.
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

        runner = next(((s, e) for s, e in scored if e is not best), None)
        return Match(
            item=best.item,
            score=round(best_score, 3),
            band=band_for(best_score, act, ask),
            runner_up=runner[1].item if runner else None,
            runner_up_score=round(runner[0], 3) if runner else 0.0,
        )
