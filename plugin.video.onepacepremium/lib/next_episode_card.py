"""Corner bar announcing the next episode while the current one plays."""
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import get_setting, log

ADDON_ID = "plugin.video.onepacepremium"
ACCENT = "FF81A6C6"

_PROGRESS = 300
# What to hand back to the player rather than swallow. Anything else simply
# closes the bar, so a second press does whatever it was going to do.
# What arrives here, mapped to what the Action builtin calls it. The two are
# not the same vocabulary: 77 is ACTION_PLAYER_FORWARD, and the builtin only
# answers to "fastforward". Names are ActionTranslator.cpp's.
#
# The keys on the left are what a *dialog* receives, which is why the arrows
# are plain moves rather than the step actions fullscreen video would get.
_FORWARD = {24: "osd", 7: "osd", 100: "osd", 103: "osd", 11: "info"}
# Kodi's keyboard map handles these in its <global> section, so they reach the
# player whether or not this bar is in the way. Touching them ourselves means
# two handlers: it pauses, we see it paused, and we helpfully resume it.
_GLOBAL = {12, 13, 79, 14, 15, 88, 89, 91, 104, 105}
# Seeking is not global, so nothing else does it while the bar is up. The
# builtins were accepted and did nothing, so it is done on the player.
_SEEK = {1: -10, 2: 10, 3: 60, 4: -60}
# Slack around the edge of the range, so seeking near it cannot make the bar
# blink in and out.
_HYSTERESIS = 5
# 101 is the right mouse button, which is Back everywhere else in Kodi.
_CLOSE_ACTIONS = (9, 10, 92, 101)
# Kodi delivers something the instant the window opens — with nothing here able
# to take focus, that arrived as a key press and shut the bar immediately.
_NOOP = 999
_SETTLE = 1.0
# Kodi's mouse actions. Moving and dragging are a nudge of the desk, not an
# instruction; a click is somebody reaching for a control we are sitting on.
# Drag, move, long click and the end-of-gesture marker. None of them are an
# instruction, and 109 was closing the bar on the way out of a click.
_MOUSE_QUIET = (106, 107, 108, 109)
_MOUSE = range(100, 110)


def _publish_lift():
    """Tell the skin how far to step up when the player's controls appear.

    Every skin puts them at a different height and Kodi offers no way to ask
    where they are, so the viewer sets it. The skin has one animation per step
    because a slide distance cannot be read from a property.
    """
    try:
        raw = int(get_setting("autoplay_card_lift") or 100)
        step = min(240, max(0, raw // 20 * 20))
    except ValueError:
        step = 100
    xbmcgui.Window(10000).setProperty("pp.lift", str(step))
    return step


def countdown_label(seconds):
    """30s, 1m 59s, 2m — minutes only once there are any."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}m" if not rest else f"{minutes}m {rest}s"


class NextEpisodeCard(xbmcgui.WindowXMLDialog):
    """Tells you what is next. Never asks, and never keeps the remote.

    Nothing in the skin can take focus, so the only thing that reaches this
    window is a key press — and every one of them gives control straight back.
    """

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self._series = kwargs.get("series", "")
        self._episode = kwargs.get("episode", "")
        self._thumb = kwargs.get("thumb", "")
        self._bar = None
        self.dismissed = False
        self.touched = False
        self.stepped_aside = False
        self.out_of_range = False
        self._said_modal = False
        self._player = None
        self._window = kwargs.get("window", 0) or 0
        self.remaining = None
        self._opened = 0.0

    def onInit(self):
        self._opened = time.monotonic()
        self.setProperty("pp.series", f"[B]Next on[/B] [COLOR {ACCENT}]{self._series}[/COLOR]")
        self.setProperty("pp.episode", self._episode)
        if self._thumb:
            try:
                self.getControl(200).setImage(self._thumb)
            except Exception:
                pass
        try:
            self._bar = self.getControl(_PROGRESS)
        except Exception:
            self._bar = None

    def onAction(self, action):
        """Hand back what we can, and get out of the way of what we cannot.

        Kodi routes everything to the window on top, so while this is up the
        player hears nothing. The few actions worth naming are put back and
        the bar stays; anything else closes it, because a bar you cannot see
        past is worse than one that ends early. Neither cancels what is next
        — only Back does that.
        """
        action_id = action.getId()
        if action_id == _NOOP or action_id in _MOUSE_QUIET:
            return
        if time.monotonic() - self._opened < _SETTLE:
            log(f"[autoplay] ignoring action {action_id} as the bar opens")
            return
        if not self._said_modal:
            # Reaching here at all means the skin's modeless tag was not
            # honoured, and everything below is the fallback.
            self._said_modal = True
            log("[autoplay] the bar is taking input, so it is not modeless here")

        # Somebody is here. That is enough to start the run over, whatever
        # they pressed and however the bar ends.
        self.touched = True

        if action_id in _CLOSE_ACTIONS:
            log(f"[autoplay] action {action_id} closed the bar")
            self.dismissed = True
            return

        if self._drive(action_id):
            return

        builtin = _FORWARD.get(action_id)
        if builtin:
            log(f"[autoplay] handing {builtin} back to the player")
            try:
                xbmc.executebuiltin(f"Action({builtin},fullscreenvideo)")
            except Exception:
                pass
            return

        # Not one we can pass on, so stand aside and let it reach the player.
        log(f"[autoplay] action {action_id} is not ours, stepping aside")
        self.stepped_aside = True

    def _drive(self, action_id):
        """Work the player directly. True if this action was ours to do."""
        if action_id in _GLOBAL:
            log(f"[autoplay] action {action_id} is Kodi's to handle, leaving it")
            return True
        if self._player is None or action_id not in _SEEK:
            return False
        try:
            where = max(0.0, self._player.getTime() + _SEEK[action_id])
            log(f"[autoplay] seeking to {where:.0f}s")
            self._player.seekTime(where)
        except Exception as exc:
            log(f"[autoplay] could not seek: {exc}")
        return True

    def _tick(self, player, total, span):
        try:
            self.remaining = max(0.0, total - player.getTime())
        except Exception:
            return
        if self.remaining > self._window + _HYSTERESIS:
            log(f"[autoplay] {self.remaining:.0f}s left, back outside the range")
            self.out_of_range = True
            return
        self.setProperty("pp.countdown", countdown_label(self.remaining))
        if self._bar and span:
            self._bar.setPercent(max(0.0, min(100.0, self.remaining * 100.0 / span)))

    def run(self, player, monitor):
        """Block until it closes or playback ends. Returns self, having noted why."""
        self._player = player
        self.show()
        try:
            total = player.getTotalTime()
            span = max(1.0, total - player.getTime())
        except Exception:
            total, span = 0.0, 0.0

        try:
            while not (self.dismissed or self.stepped_aside or self.out_of_range):
                if total:
                    self._tick(player, total, span)
                if not player.isPlaying() or monitor.waitForAbort(0.2):
                    break
        finally:
            self.close()
        return self


def show_next_episode(series, episode, thumb, player, monitor, window=0):
    """Announce it. Returns (cancelled, anyone was there, still in range)."""
    log(f"[autoplay] the bar lifts {_publish_lift()}px over the player's controls")
    addon_path = xbmcvfs.translatePath(
        xbmcaddon.Addon(ADDON_ID).getAddonInfo("path")
    )
    card = NextEpisodeCard(
        "next_episode.xml", addon_path, "Default", "1080i",
        series=series, episode=episode, thumb=thumb, window=window,
    )
    try:
        card.run(player, monitor)
        return card.dismissed, card.touched, card.out_of_range
    finally:
        del card
