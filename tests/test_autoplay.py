"""Deciding whether another episode should start, and when to ask instead."""
import harness

harness.setup()

import kodistub
from lib import playback, still_watching

SRC = (harness.ADDON / "lib" / "playback.py").read_text(encoding="utf-8")
SETTINGS = (harness.ADDON / "resources" / "settings.xml").read_text(encoding="utf-8")

CARD = {"series": "One Pace", "episode": "1x02 The Great Swordsman",
        "thumb": "", "title": "The Great Swordsman", "url": "plugin://x"}


class Monitor:
    def waitForAbort(self, secs=0):
        return False


def announce(dismissed, touched=False, left_range=False):
    """Run the decision with a bar that ended for a given reason."""
    import lib.next_episode_card as card
    card.show_next_episode = lambda *a, **k: (dismissed, touched, left_range)
    return playback._announce_next_episode(CARD, None, Monitor(), 300)[:2]


print("=== what the bar reports back ===")
for label, dismissed, want in (("stepped aside or ran out", False, False),
                               ("pressed Back", True, True)):
    cancelled, _ = announce(dismissed)
    print(f"  {label:<26} -> {'cancelled' if cancelled else 'still queued'}")
    assert cancelled is want, (label, cancelled)

# Pausing to fetch a drink says somebody is here. It is not a decision about
# the next episode, so it must not cancel one.
cancelled, touched = announce(dismissed=False, touched=True)
print(f"  paused, bar stepped aside  -> still queued, counted as present={touched}")
assert not cancelled and touched

print()
print("=== whether the episode ran out is the player's to say ===")
# A bar that stepped aside early cannot know how the episode ended, so the
# decision waits for playback and uses ended_naturally rather than a guess.
tail = SRC[SRC.index("if queued_next:"):]
tail = tail[:tail.index("# Replaying something")]
# The callbacks are queued for Python, so isPlaying() goes false before one
# arrives. Reading a flag straight away made every episode look stopped.
assert "why_it_finished(" in tail, "the outcome is read before Kodi has said"
assert tail.index("why_it_finished") < tail.index("_may_start_next"),     "it decides before asking how playback ended"

# Not knowing why it stopped must not become "so it probably finished".
assert "_ENDED_WITHIN" not in SRC, "a stop near the end would count as finishing"
assert "why != ENDED" in tail, "something other than a clean end starts the next one"
outcomes = [w for w in ("ENDED", "STOPPED", "ERROR", "UNKNOWN") if f'{w}, ' in SRC or f'{w} =' in SRC]
print(f"  outcomes: {outcomes}")
assert len(outcomes) == 4, outcomes

# Waiting on the event, not on the predicate.
mon = SRC[SRC.index("class _WatchMonitor"):SRC.index("def _monitor_playback(")]
assert "threading.Event()" in mon, "still polling instead of waiting"
for cb in ("onPlayBackEnded", "onPlayBackStopped", "onPlayBackError"):
    assert f"def {cb}(" in mon, f"{cb} is not distinguished"
# Blocking on the event alone starves the machinery that delivers it, and
# the answer then never arrives — which reads as unknown and starts nothing.
assert "self.finished.wait(" not in mon, "a blocked thread cannot receive the callback"
assert "monitor.waitForAbort(" in mon, "nothing gives Kodi a chance to deliver it"
print("  waits on the callback, and anything but a clean end fails closed  OK")

print()
print("=== how much of a short episode the bar may cover ===")
# 300s on a five-minute episode used to mean the bar was up for half of it.
for total, setting, want in ((1440, 300, 300), (1200, 300, 300), (600, 300, 150),
                             (300, 300, 75), (1440, 20, 20)):
    window = min(setting, total / playback._SHORTEST_SHARE)
    print(f"  {total // 60:>2}min episode, set to {setting:>3}s -> shows for {window:.0f}s"
          f" ({window / total * 100:.0f}% of it)")
    assert window == want, (total, window)
assert playback._SHORTEST_SHARE == 4, "a quarter was the agreed share"

print()
print("=== the bar follows the range, both ways ===")
# Seeking back used to leave it up reading 12m 25s, and once it had been shown
# it never came back.
CARD_SRC = (harness.ADDON / "lib" / "next_episode_card.py").read_text(encoding="utf-8")
tick = CARD_SRC[CARD_SRC.index("def _tick("):]
tick = tick[:tick.index(chr(10) + "    def ")]
assert "self.out_of_range = True" in tick, "seeking back leaves the bar up"
assert "_HYSTERESIS" in tick, "it would blink in and out at the boundary"

loop = SRC[SRC.index("in_range = "):SRC.index("# A newer session")]
assert "may_show = True" in loop, "once shown it could never come back"
assert "up_next = None" in loop and loop.index("if dismissed") < loop.index("up_next = None"),     "something other than saying no would stop it coming back"
assert "may_show = left_range" in loop, "stepping aside would pop it straight back up"
print("  hides when you seek away, returns when you come back, and only Back stops it  OK")

print()
print("=== the bar lets go of the player when it closes ===")
# Seeking happens on the callback thread, so the bar has to hold a player to
# do it. Kodi decides when a window object is really gone, so the reference is
# handed back explicitly rather than left for the window to carry off with it.
run_src = CARD_SRC[CARD_SRC.index("    def run(self, player, monitor):"):]
assert "self._player = None" in run_src, "the bar keeps hold of the player"
assert run_src.index("self._player = None") < run_src.index("self.close()"),     "let go after the window goes, rather than before"
print("  dropped in the finally, ahead of the close  OK")

print()
print("=== the run only counts episodes nobody touched ===")
kodistub._WINDOW_PROPS.clear()
assert still_watching.episodes_in_a_row() == 0
for n in (1, 2, 3):
    assert still_watching.note_episode(touched=False) == n
print(f"  three unattended -> {still_watching.episodes_in_a_row()}")
assert still_watching.note_episode(touched=True) == 0, "a key press should start it over"
print("  then somebody presses a key -> 0  OK")

print()
print("=== picking an episode by hand ends the run ===")
# Two unattended episodes, then the viewer goes to the menu and chooses one.
# Without this the next hand-off would be counted as the third in a row and
# ask whether somebody who just pressed play is still there.
kodistub._WINDOW_PROPS.clear()
for _ in range(2):
    still_watching.note_episode(touched=False)
print(f"  after two unattended: {still_watching.episodes_in_a_row()}")
started = SRC[SRC.index("    play_next_url = None"):]
started = started[:started.index("# Poll every 1 s")]
assert "if not autoplay:" in started and "_sw.reset()" in started,     "a hand-picked episode carries the old run forward"
still_watching.reset()
assert still_watching.episodes_in_a_row() == 0

# Two ways reach the player, and only the streaming one carried the flag. A
# run of downloaded episodes therefore looked like somebody pressing play on
# each one, and the count never got past its first step.
ER = (harness.ADDON / "lib" / "episode_routes.py").read_text(encoding="utf-8")
resume = ER[ER.index("def check_resume("):]
resume = resume[:resume.index(chr(10) + "def ")]
assert "local_playback(" in resume and "_choose_stream(" in resume, "both paths gone"
assert 'chosen["autoplay"] = "1"' in resume,     "the copy on disk loses the flag, so every hand-off resets the run"
assert resume.index("local_playback") < resume.index('chosen["autoplay"]'),     "the flag must be put back after whichever path chose the stream"
print("  the flag survives whichever way the episode is played  OK")

print()
print("=== the window that showed the question is the one that closes it ===")
SW_SRC = (harness.ADDON / "lib" / "still_watching.py").read_text(encoding="utf-8")
clicks = SW_SRC[SW_SRC.index("def onClick("):SW_SRC.index("def run(")]
assert "self.close()" not in clicks,     "closing from the callback thread is the direction that is unreliable"
assert "_answered = True" in clicks
run_body = SW_SRC[SW_SRC.index("def run("):]
assert "self.close()" in run_body, "nothing ever closes it"
assert "_TICK" in run_body, "a press would sit on screen for the rest of the second"
print("  answered on the callback thread, closed on the one that showed it  OK")

print()
print("=== the gate ===")


def gate(limit, run_so_far, answer):
    kodistub._WINDOW_PROPS.clear()
    playback._int_setting = lambda key, fb, lo, hi: limit
    for _ in range(run_so_far):
        still_watching.note_episode(touched=False)
    asked = []
    still_watching.ask = lambda episodes, episode, monitor: asked.append(episodes) or answer
    allowed = playback._may_start_next(CARD, Monitor(), touched=False)
    return allowed, asked


_real_int, _real_ask = playback._int_setting, still_watching.ask

allowed, asked = gate(limit=3, run_so_far=0, answer=False)
print(f"  first episode of a run   -> {'plays' if allowed else 'stops'}, asked={asked}")
assert allowed and not asked, "it should not ask on the first one"

allowed, asked = gate(limit=3, run_so_far=2, answer=False)
print(f"  third in a row, no reply -> {'plays' if allowed else 'stops'}, asked={asked}")
assert not allowed and asked == [3], (allowed, asked)

allowed, asked = gate(limit=3, run_so_far=2, answer=True)
print(f"  third in a row, answered -> {'plays' if allowed else 'stops'}, asked={asked}")
assert allowed and asked == [3]
assert still_watching.episodes_in_a_row() == 0, "answering should start the run over"

allowed, asked = gate(limit=0, run_so_far=50, answer=False)
print(f"  turned off, 50 in a row  -> {'plays' if allowed else 'stops'}, asked={asked}")
assert allowed and not asked, "0 should mean never ask"

playback._int_setting, still_watching.ask = _real_int, _real_ask
kodistub._WINDOW_PROPS.clear()

print()
print("=== the settings behind it ===")
assert 'id="autoplay_prompt_secs"' in SETTINGS and 'range="5,5,300"' in SETTINGS,     "the slider still stops at 90"
# A slider the code then clamps lower is a slider that lies.
clamp = SRC[SRC.index('_int_setting("autoplay_prompt_secs"'):]
clamp = clamp[:clamp.index(")") + 1]
print(f"  {clamp}")
assert clamp.endswith("300)"), "the slider goes to 300 but the code caps it lower"
assert 'id="autoplay_still_watching"' in SETTINGS and 'range="0,1,10"' in SETTINGS
print("  card up to 300s, ask after 0-10 episodes  OK")

print()
print("all assertions passed")
