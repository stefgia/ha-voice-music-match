"""Constants for Music Match."""

from datetime import timedelta

DOMAIN = "ha_voice_music_match"

EVENT_DECISION = f"{DOMAIN}_decision"

MUSIC_ASSISTANT_DOMAIN = "music_assistant"

REFRESH_INTERVAL = timedelta(hours=1)
# Used until the first library load succeeds.
RETRY_DELAY = timedelta(minutes=5)
# get_library's maximum page size.
PAGE_SIZE = 500

SATELLITE_IDLE_TIMEOUT = 20.0
# Satellites can briefly report idle before the pipeline ends.
SATELLITE_IDLE_SETTLE = 0.7

CONF_LANGUAGE = "language"
CONF_ACT_THRESHOLD = "act_threshold"
CONF_ASK_THRESHOLD = "ask_threshold"
CONF_REPLY_PLAYING = "reply_playing"
CONF_REPLY_NOT_FOUND = "reply_not_found"
CONF_REPLY_DID_YOU_MEAN = "reply_did_you_mean"
CONF_ANSWERS_YES = "answers_yes"
CONF_ANSWERS_NO = "answers_no"

DEFAULT_REPLY_PLAYING = (
    "Playing {{ name }}{% if artist %} by {{ artist }}{% endif %}"
    "{% if area %} in the {{ area | lower }}{% endif %}"
)
DEFAULT_REPLY_NOT_FOUND = "I couldn't find {{ heard }}."
DEFAULT_REPLY_DID_YOU_MEAN = "Did you mean {{ name }}{% if artist %} by {{ artist }}{% endif %}?"
DEFAULT_ANSWERS_YES = ["yes", "yeah", "yep", "yes please", "sure", "correct", "that's right", "play it"]
DEFAULT_ANSWERS_NO = ["no", "nope", "no thanks", "cancel", "never mind"]
