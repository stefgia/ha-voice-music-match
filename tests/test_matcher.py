"""Matcher unit tests: no Home Assistant involved."""

import pytest

from custom_components.ha_voice_music_match import matcher as matcher_module
from custom_components.ha_voice_music_match.matcher import (
    ACT,
    ALBUM,
    MARGIN,
    ARTIST,
    PLAYLIST,
    TRACK,
    Band,
    LibraryItem,
    Matcher,
    normalise,
    strip_filler,
)

ITEMS = [
    LibraryItem(ARTIST, "Gangstagrass", "a:1"),
    LibraryItem(ARTIST, "Mozart", "a:2"),
    LibraryItem(ARTIST, "Kansas", "a:3"),
    LibraryItem(ARTIST, "Beyoncé", "a:4"),
    LibraryItem(ARTIST, "Guns N' Roses", "a:5"),
    LibraryItem(ALBUM, "Rappalachia", "b:1", ("Gangstagrass",)),
    LibraryItem(TRACK, "Kansas", "t:1", ("Some Band",)),
    LibraryItem(TRACK, "Carry On Wayward Son", "t:2", ("Kansas",)),
    LibraryItem(TRACK, "Dust in the Wind (Remastered 2002)", "t:3", ("Kansas",)),
    LibraryItem(PLAYLIST, "Morning Mix", "p:1"),
]


def matcher() -> Matcher:
    return Matcher(ITEMS)


def test_normalise() -> None:
    assert normalise("The Beatles") == "beatles"
    assert normalise("Beyoncé") == "beyonce"
    assert normalise("Guns N' Roses") == "guns n roses"
    assert normalise("Dust in the Wind (Remastered 2002)") == "dust in the wind"
    assert normalise("Song feat. Someone") == "song"
    assert normalise("Simon & Garfunkel") == "simon and garfunkel"


def test_strip_filler() -> None:
    assert strip_filler("some music by gangstagrass") == "gangstagrass"
    assert strip_filler("my morning mix") == "morning mix"


def test_whisper_mishearings_find_the_artist() -> None:
    """The transcripts Whisper actually produced for "Gangstagrass"."""
    for heard in ("Gaza Grass", "gangster grass", "gangsta grass", "Gangstagras"):
        match = matcher().match(heard)
        assert match is not None
        assert match.item.name == "Gangstagrass", heard
        assert match.band is not Band.NONE, heard


def test_spacing_difference_is_exact() -> None:
    match = matcher().match("gangsta grass")
    assert match is not None
    assert match.item.uri == "a:1"
    assert match.score >= ACT


def test_filler_and_punctuation() -> None:
    match = matcher().match("some guns and roses")
    assert match is not None
    assert match.item.name == "Guns N' Roses"


def test_song_by_artist() -> None:
    match = matcher().match("carry on wayward sun by kansas")
    assert match is not None
    assert match.item.uri == "t:2"
    assert match.band is Band.ACT


def test_brackets_ignored_on_titles() -> None:
    match = matcher().match("dust in the wind", TRACK)
    assert match is not None
    assert match.item.uri == "t:3"


def test_artist_wins_tie_against_song() -> None:
    """A song and an artist share a name: the artist is chosen."""
    match = matcher().match("Kansas")
    assert match is not None
    assert match.item.media_type == ARTIST


def test_media_type_restricts_candidates() -> None:
    match = matcher().match("Kansas", TRACK)
    assert match is not None
    assert match.item.uri == "t:1"


def test_playlist() -> None:
    match = matcher().match("my morning mix")
    assert match is not None
    assert match.item.uri == "p:1"
    assert match.band is Band.ACT


def test_unrelated_request_is_not_acted_on() -> None:
    match = matcher().match("Taylor Swift")
    assert match is not None
    assert match.band is not Band.ACT


def test_duplicate_names_are_not_a_rival() -> None:
    """"Kansas" is in the library twice, as an artist and as a track: one answer."""
    match = matcher().match("Kansus")
    assert match is not None
    assert match.item.name == "Kansas"
    assert match.runner_up is not None
    assert normalise(match.runner_up.name) != "kansas"
    assert match.band is Band.ACT


def test_a_close_rival_asks_instead_of_playing() -> None:
    items = [*ITEMS, LibraryItem(ALBUM, "Morning Mixtape", "b:2", ("Someone",))]
    match = Matcher(items).match("Mourning Mixed")
    assert match is not None
    assert match.score >= ACT
    assert match.score - match.runner_up_score < MARGIN
    assert match.band is Band.ASK


def test_no_margin_plays_the_best_score() -> None:
    items = [*ITEMS, LibraryItem(ALBUM, "Morning Mixtape", "b:2", ("Someone",))]
    match = Matcher(items).match("Mourning Mixed", margin=0.0)
    assert match is not None
    assert match.band is Band.ACT


def test_an_exact_name_plays_past_a_close_rival() -> None:
    items = [*ITEMS, LibraryItem(ARTIST, "Mozzart", "a:9")]
    match = Matcher(items).match("Mozart", margin=0.2)
    assert match is not None
    assert match.score == 1.0
    assert match.score - match.runner_up_score < 0.2
    assert match.band is Band.ACT


def test_spacing_variants_are_not_a_rival() -> None:
    """The scores ignore spaces, so the rival check has to as well."""
    items = [*ITEMS, LibraryItem(ALBUM, "Gangsta Grass", "b:2", ("Someone",))]
    match = Matcher(items).match("Gaza Grass")
    assert match is not None
    assert match.runner_up is not None
    assert normalise(match.runner_up.name).replace(" ", "") != "gangstagrass"
    assert match.band is Band.ACT


def test_no_margin_plays_after_the_artist_tie_break() -> None:
    """An artist promoted over a slightly higher track still plays with the margin off."""
    items = [
        LibraryItem(TRACK, "Allegro Moderato Cantabile", "t:9", ("Someone",)),
        LibraryItem(ARTIST, "Allegro Moderato Cantabila", "a:9"),
    ]
    matcher_ = Matcher(items)
    off = matcher_.match("Alegro Moderato Cantabile", margin=0.0)
    assert off is not None
    assert off.item.media_type == ARTIST
    assert off.runner_up_score > off.score
    assert off.band is Band.ACT
    # With the margin on, a differently-named track scoring higher is a real rival.
    on = matcher_.match("Alegro Moderato Cantabile")
    assert on is not None
    assert on.band is Band.ASK


def test_an_exact_track_is_not_displaced_by_a_near_artist() -> None:
    name = "Symphony No 9 in D Minor Choral Finale Allegro"
    items = [LibraryItem(TRACK, name, "t:9", ("Someone",)), LibraryItem(ARTIST, name[:-1] + "a", "a:9")]
    match = Matcher(items).match(name)
    assert match is not None
    assert match.item.uri == "t:9"
    assert match.score == 1.0
    assert match.band is Band.ACT


def test_runner_up_is_reported() -> None:
    match = matcher().match("Gangstagrass")
    assert match is not None
    assert match.runner_up is not None
    assert match.runner_up_score < match.score


def test_empty_query_and_library() -> None:
    assert matcher().match("  ") is None
    assert Matcher([]).match("anything") is None


def test_thresholds_are_parameters() -> None:
    match = matcher().match("Gaza Grass")
    assert match is not None
    assert match.band is Band.ACT
    strict = matcher().match("Gaza Grass", act=0.99, ask=0.98)
    assert strict is not None
    assert strict.item == match.item
    assert strict.band is Band.NONE


@pytest.mark.parametrize(
    ("heard", "media_type"),
    [
        ("Gaza Grass", None),
        ("carry on wayward son by kansas", None),
        ("rap a latcher", None),
        ("kansas", TRACK),
        ("morning mix", PLAYLIST),
    ],
)
def test_prefilter_agrees_with_full_scan(
    monkeypatch: pytest.MonkeyPatch, heard: str, media_type: str | None
) -> None:
    """Big libraries only score the closest items, and still pick the same one."""
    full = matcher().match(heard, media_type)
    monkeypatch.setattr(matcher_module, "PREFILTER_MIN_ITEMS", 0)
    monkeypatch.setattr(matcher_module, "PREFILTER_KEEP", 3)
    narrowed = matcher().match(heard, media_type)
    assert narrowed is not None and full is not None
    assert (narrowed.item, narrowed.score, narrowed.band) == (full.item, full.score, full.band)


def test_prefilter_with_nothing_in_common_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(matcher_module, "PREFILTER_MIN_ITEMS", 0)
    assert matcher().match("jjj") is None
