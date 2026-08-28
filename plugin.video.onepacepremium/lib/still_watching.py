"""Asks whether anyone is still there, after a run of episodes nobody touched."""
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import log

ADDON_ID = "plugin.video.onepacepremium"
_HOME = 10000
_COUNT = "pp.binge.count"

_KEEP_GOING = 11
_STOP_BUTTON = 10
_CLOSE_ACTIONS = (9, 10, 92)
# Long enough to pick up a remote, short enough not to sit there all night.
_COUNTDOWN = 30
_TICK = 0.2


def _window():
    return xbmcgui.Window(_HOME)


def episodes_in_a_row():
    try:
        return int(_window().getProperty(_COUNT) or 0)
    except ValueError:
        return 0


def note_episode(touched):
    """Count another episode, or start over because somebody was there."""
    count = 0 if touched else episodes_in_a_row() + 1
    _window().setProperty(_COUNT, str(count))
    return count


def reset():
    _window().clearProperty(_COUNT)


def _how_many(episodes):
    """The whole sentence, so one episode does not read as "1 episodes"."""
    if episodes <= 1:
        return "An episode has played without a word."
    return f"{episodes} episodes have played without a word."


class StillWatching(xbmcgui.WindowXMLDialog):
    """Nothing is playing by now, so this one is a real question with buttons."""

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self._episodes = kwargs.get("episodes", 0)
        self._episode = kwargs.get("episode", "")
        # Silence stops. Someone asleep should not wake to another season gone.
        self.keep_going = False
        self._answered = False

    def onInit(self):
        self.setProperty("pp.episodes", _how_many(self._episodes))
        self.setProperty("pp.episode", self._episode)
        self.setFocusId(_KEEP_GOING)

    def onClick(self, control_id):
        self.keep_going = control_id == _KEEP_GOING
        # Answered, not closed: the thread that showed this window is the one
        # that closes it, the same way the setup dialog does.
        self._answered = True

    def onAction(self, action):
        if action.getId() in _CLOSE_ACTIONS:
            self._answered = True

    def run(self, monitor):
        self.show()
        left, waited = _COUNTDOWN, 0.0
        try:
            self.setProperty("pp.countdown", str(left))
            # Polled far faster than it counts, so a button press is not left
            # sitting on screen for the rest of the second.
            while not self._answered and left > 0:
                if monitor.waitForAbort(_TICK):
                    break
                waited += _TICK
                if waited >= 1.0:
                    waited, left = 0.0, left - 1
                    self.setProperty("pp.countdown", str(left))
        finally:
            self.close()
        return self.keep_going


def ask(episodes, episode, monitor):
    """True to carry on. Silence, Escape or Stop all mean stop."""
    addon_path = xbmcvfs.translatePath(
        xbmcaddon.Addon(ADDON_ID).getAddonInfo("path")
    )
    window = StillWatching(
        "still_watching.xml", addon_path, "Default", "1080i",
        episodes=episodes, episode=episode,
    )
    try:
        answer = window.run(monitor)
    except Exception as exc:
        log(f"[autoplay] still-watching window failed: {exc}")
        answer = False
    finally:
        del window
    log(f"[autoplay] after {episodes} in a row: "
        + ("carrying on" if answer else "stopping"))
    return answer
