"""Constants for Music Match."""

from datetime import timedelta

DOMAIN = "ha_voice_music_match"

# Fired for every request the handler matches: what was heard, what it picked,
# the score and runner-up. The log thresholds are tuned from.
EVENT_DECISION = f"{DOMAIN}_decision"

MUSIC_ASSISTANT_DOMAIN = "music_assistant"

REFRESH_INTERVAL = timedelta(hours=1)
# Sooner retry while there is no library at all (MA still starting, say).
RETRY_DELAY = timedelta(minutes=5)
# music_assistant.get_library returns at most this many items per call.
PAGE_SIZE = 500

# How long "did you mean" waits for the satellite to finish speaking the first
# reply before asking.
SATELLITE_IDLE_TIMEOUT = 20.0
# The satellite can report idle for a moment before the pipeline really ends,
# so idle must hold this long.
SATELLITE_IDLE_SETTLE = 0.7

# Options, all changeable after setup. Missing options fall back to these.
CONF_LANGUAGE = "language"
CONF_ACT_THRESHOLD = "act_threshold"
CONF_ASK_THRESHOLD = "ask_threshold"
CONF_REPLY_PLAYING = "reply_playing"
CONF_REPLY_NOT_FOUND = "reply_not_found"
CONF_REPLY_DID_YOU_MEAN = "reply_did_you_mean"
CONF_ANSWERS_YES = "answers_yes"
CONF_ANSWERS_NO = "answers_no"

# Reply templates get: heard, name, artist (songs and albums only), media_type, area.
DEFAULT_REPLY_PLAYING = (
    "Playing {{ name }}{% if artist %} by {{ artist }}{% endif %}"
    "{% if area %} in the {{ area | lower }}{% endif %}"
)
DEFAULT_REPLY_NOT_FOUND = "I couldn't find {{ heard }}."
DEFAULT_REPLY_DID_YOU_MEAN = "Did you mean {{ name }}{% if artist %} by {{ artist }}{% endif %}?"
DEFAULT_ANSWERS_YES = ["yes", "yeah", "yep", "yes please", "sure", "correct", "that's right", "play it"]
DEFAULT_ANSWERS_NO = ["no", "nope", "no thanks", "cancel", "never mind"]
