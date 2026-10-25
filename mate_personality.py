"""
mate_personality.py - the desktop mate's voice (pure Python, no Qt).

The mate is a dry, sleepy night-cat: deadpan, a little smug, quietly fond of
you. It never changes what Aether *does* - only the small things the mate says
for itself in its speech bubble (greeting, being petted, waking up, dozing off,
idle muttering, a quip while Aether works, errors). Real replies from Aether
are always shown as-is.

    voice = Personality()
    voice.pick("pet")                  # -> "Acceptable."
    voice.pick("greet", hour=2)        # -> a late-night greeting
    voice.petted()                     # call once per click; True when it's too much

Events (see LINES): greet, pet, pet_many, wake, sleep, thinking, working,
error, chatter. `greet` and `chatter` also have time-of-day variants.

Every line is one short sentence group (well under the bubble's 140-char cap).
Every `error` line keeps "went wrong - check the log" so the bubble still tells
the user where to look.

Config (read by MainWindow, optional):
    desktop_mate_personality   bool, default true - false = plain, silent mate
"""
from __future__ import annotations

import random
import time
from datetime import datetime

LINES: dict[str, tuple[str, ...]] = {
    "pet": (
        "Acceptable.",
        "Mrrp.",
        "...Fine. Two more seconds.",
        "Purring. Don't tell anyone.",
        "That's the spot.",
        "Careful. The hood stays on.",
    ),
    "pet_many": (
        "Okay. That's enough.",
        "I have a limit. You found it.",
        "Hands. Away. Please.",
    ),
    "wake": (
        "I wasn't asleep. Resting my eyes.",
        "Mm. I'm up. Mostly.",
        "Oh. Hi. What did I miss?",
    ),
    "sleep": (
        "Nothing to do. Napping.",
        "Zzz... wake me if something breaks.",
        "Poke me if you need me.",
    ),
    "thinking": (
        "Hm. Let me look into that.",
        "Thinking. Don't rush the cat.",
        "One sec. Whiskers are tingling.",
    ),
    "working": (
        "On it.",
        "Paws on keys.",
        "Working. Try not to watch.",
    ),
    "error": (
        "Something went wrong - check the log. Not my fault.",
        "Well. Something went wrong - check the log.",
        "Something went wrong - check the log. I blame the dog.",
    ),
    "chatter": (
        "I rearranged nothing. Just watching.",
        "Hydrate. Humans forget.",
        "Your cursor and I have an understanding.",
        "Stretch. Your spine says hi.",
        "I could knock something off the desk. I won't. Today.",
        "Quiet in here. I like it.",
        "Ask me something. I'm bored.",
    ),
}

# Time-of-day variants. Keys: morning 5-11, day 11-17, evening 17-23, late 23-5.
TIMED_LINES: dict[str, dict[str, tuple[str, ...]]] = {
    "greet": {
        "morning": (
            "Morning. I was up all night, so you're welcome.",
            "Morning. Coffee first, questions second.",
        ),
        "day": (
            "Back at it. I'll supervise.",
            "There you are.",
        ),
        "evening": (
            "Evening. Prime working hours, if you're a cat.",
            "Evening. The good light is almost gone. My favorite.",
        ),
        "late": (
            "It's late. I'm thriving. You should probably sleep.",
            "Midnight oil? I respect it. Briefly.",
        ),
    },
    "chatter": {
        "late": (
            "Still here. So am I.",
            "The moon's up. I'm at my best.",
        ),
    },
}


def day_part(hour: int) -> str:
    """Bucket an hour (0-23) into morning / day / evening / late."""
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "day"
    if 17 <= hour < 23:
        return "evening"
    return "late"


class Personality:
    """Picks lines without repeating the previous one for the same event, and
    tracks how fast the user is petting so the cat can lose patience."""

    PET_WINDOW_S = 25.0   # this many seconds...
    PET_LIMIT = 5         # ...with this many pets is "too much"

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self._last: dict[str, str] = {}
        self._pets: list[float] = []

    def pick(self, event: str, hour: int | None = None) -> str:
        """A line for `event`, or "" for an unknown event. Time-of-day lines
        are mixed into the pool for events that have them."""
        pool = list(LINES.get(event, ()))
        timed = TIMED_LINES.get(event)
        if timed is not None:  # e.g. `greet` is *only* time-of-day lines
            h = datetime.now().hour if hour is None else hour
            pool = list(timed.get(day_part(h), ())) + pool
        if not pool:
            return ""
        last = self._last.get(event)
        choices = [p for p in pool if p != last] or pool
        line = self._rng.choice(choices)
        self._last[event] = line
        return line

    def petted(self, now: float | None = None) -> bool:
        """Record one pet. True once the user has petted PET_LIMIT times within
        PET_WINDOW_S (and the counter resets, so the next pets start fresh)."""
        t = time.monotonic() if now is None else now
        self._pets = [x for x in self._pets if t - x <= self.PET_WINDOW_S]
        self._pets.append(t)
        if len(self._pets) >= self.PET_LIMIT:
            self._pets.clear()
            return True
        return False
