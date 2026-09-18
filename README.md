# Music Match

Music Match is a Home Assistant integration for voice commands like "Play Gangstagrass in the bedroom". It checks the artist, album, song or playlist name against your Music Assistant library before playing, so a name that speech-to-text got wrong still plays the right thing.

Speech-to-text often mangles band names. "Play Gangstagrass in the bedroom" can come out as "Play Gaza Grass in the bedroom". Home Assistant's built-in play command passes "Gaza Grass" to Music Assistant's search, which finds nothing. Music Match compares "Gaza Grass" with the names in your library, finds Gangstagrass and hands that name to the play command, which plays it. Music Match then says "Playing Gangstagrass in the bedroom".

## Requirements

- Home Assistant 2026.9.0 or newer.
- The [Music Assistant integration](https://www.home-assistant.io/integrations/music_assistant/).

Music Match reads the library through the Music Assistant integration, so it needs no extra accounts or passwords.

## Installation

1. In HACS, open the menu in the top right and choose **Custom repositories**.
2. Add `https://github.com/stefgia/ha-voice-music-match` with the type **Integration**.
3. Search HACS for **Music Match**, download it and restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration** and add **Music Match**.

Nothing needs configuring to start. The defaults work for English.

## What happens to a play request

Music Match changes the handler behind Home Assistant's built-in play command. The sentences stay the same. Requests from an AI conversation agent also go through Music Match, because the agent uses the same play command.

Music Match only corrects the name, and Home Assistant's own handler does the rest. Music Match handles a request when both of these are true:

- It is in the language set in the options.
- It asks for music: an artist, album, song or playlist, or no type at all.

Home Assistant's own handler takes every other request, unchanged.

Music Match scores each name in the library from 0 to 1. It compares both spelling (ignoring spaces, so "gangsta grass" matches "Gangstagrass") and a rough sound key (so "Gaza Grass" is close to "Gangstagrass"). "Song by artist" requests use both parts. If a song scores almost the same as an artist or album, the artist or album wins. Then:

- A score at or above the play threshold (default 0.70) plays the item. Music Match passes the matched name and its type (artist, album, song or playlist) to Home Assistant's handler in place of what was heard.
- A winner that beats the closest item under a different name by less than the margin (default 0.08) asks instead of playing. A name the library holds several times over, such as a song on three albums, counts once, and a name heard exactly always plays.
- A score between the ask threshold (default 0.62) and the play threshold asks first. On a voice assistant, Music Match says "I couldn't find Gasket Grass.", then asks "Did you mean Gangstagrass?" and plays it if you say yes. A typed request gets both sentences in one reply and nothing is played.
- A lower score goes to Music Assistant's search with the name as it was heard, as if Music Match were not installed.

Music Match loads the library from every Music Assistant server when Home Assistant starts and again every hour. If a server can't be reached, Music Match keeps the items from its last successful load and still uses the other servers. With more than 5,000 items it scores only the 1,000 names that share the most letter groups with the request. A 100,000-item library then takes about 45 ms per request.

## Options

Open **Settings > Devices & services > Music Match > Configure**. Changes apply to the next request.

| Option | Default | What it does |
| --- | --- | --- |
| Language | Home Assistant's language | Requests in this language are matched. Others go to Home Assistant's handler. |
| Play threshold | 0.70 | Lowest score that plays straight away. |
| Ask threshold | 0.62 | Lowest score that asks "did you mean". Must not be above the play threshold. |
| Margin over the runner-up | 0.08 | How far ahead of the closest differently-named item the winner must be to play without asking. 0 turns it off. |
| Reply when playing | `Playing {{ name }}{% if artist %} by {{ artist }}{% endif %}{% if area %} in the {{ area \| lower }}{% endif %}` | Spoken once the item starts. |
| Reply when unsure | `I couldn't find {{ heard }}.` | Spoken before the question. |
| Did-you-mean question | `Did you mean {{ name }}{% if artist %} by {{ artist }}{% endif %}?` | The question itself. |
| Yes answers | yes, yeah, yep, yes please, sure, correct, that's right, play it | Phrases that play the item. |
| No answers | no, nope, no thanks, cancel, never mind | Phrases that cancel. |

Replies are [Home Assistant templates](https://www.home-assistant.io/docs/configuration/templating/) with these variables:

- `heard`: the name as speech-to-text wrote it.
- `name`: the matched item's name.
- `artist`: the first artist of a matched song or album. Empty for artists and playlists.
- `media_type`: `artist`, `album`, `track` or `playlist`.
- `area`: the area named in the request, if any.

A broken template falls back to the default reply and logs a warning.

If you raise the thresholds, Music Match plays wrong items less often and asks or falls back to Music Assistant's search more often. Lowering them does the opposite. To see the scores your requests get, listen for the `ha_voice_music_match_decision` event under **Developer tools > Events**. Music Match fires it for every request it scores, with the heard name, the chosen item, its score and the runner-up.

### Tuning for your library

The defaults come from 916 recorded requests against a 2,400-item library, transcribed locally by faster-whisper. They hold up across speech-to-text quality: the same 0.70 was the right cut-off for both a `tiny` and a `small` model, and a weaker model mainly played fewer right answers rather than more wrong ones.

Library size matters more. The bigger the library, the more often two unrelated names both clear the play threshold, so on the same corpus grown to 100,000 items, a quarter of the plays were wrong at the default 0.70 with no margin, against a twentieth at 2,400 items. The margin is what holds that in check: at 100,000 items it brought wrong plays back under a twentieth, at the cost of asking more often.

If Music Match plays the wrong thing on a large library, raise the margin before the play threshold. The margin only costs you the requests that had a real rival, where the threshold costs you every uncertain request. To see the scores your own requests get, listen for the `ha_voice_music_match_decision` event under **Developer tools > Events**. Music Match fires it for every request it scores, with the heard name, the chosen item, its score and the runner-up.

## Other languages

Set the language option and write the replies and answers in that language. Some of the matching is built for English:

- "Song by artist" is split on the English word "by". In other languages the whole request is compared with each name, so a song still matches but the artist does not help choose between songs.
- Leading words such as "some", "the" and "music by" are removed before matching. Other languages keep theirs, which lowers scores a little.
- The sound key follows English spelling. A name written in another alphabet does not match a name written in Latin letters. Those requests go to Music Assistant's search.

A household with voice assistants in two languages can set Music Match to one of them. Requests in the other language use Home Assistant's own replies, which you can change with a `custom_sentences` file:

```yaml
# config/custom_sentences/de/music.yaml
language: de
responses:
  intents:
    HassMediaSearchAndPlay:
      default: "Ich spiele {{ slots.media.title }}"
```

## Things to know

- At startup Home Assistant logs `Intent HassMediaSearchAndPlay is being overwritten`. That is Music Match replacing the handler. Removing the integration puts Home Assistant's handler back.
- An AI conversation agent gets Music Match's reply but writes its own final sentence, so it may word the reply differently.
- Another integration that also replaces the play command's handler conflicts with Music Match. Whichever loads last wins.

## Development

Tests run against Home Assistant 2026.9.2 on Python 3.14:

```sh
uv venv -p 3.14 .venv
uv pip install -p .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

To try a change on a running Home Assistant without HACS, run `scripts/deploy.sh /path/to/config` and restart Home Assistant.

## License

MIT. See [LICENSE](LICENSE).
