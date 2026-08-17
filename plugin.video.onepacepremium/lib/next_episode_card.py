"""Corner card offering the next episode while the current one plays."""
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "plugin.video.onepacepremium"
ACCENT = "FF81A6C6"

_WATCH = 11
_PROGRESS = 300
_CLOSE_ACTIONS = (9, 10, 92)  # back / escape / previous menu


class NextEpisodeCard(xbmcgui.WindowXMLDialog):
    """Shows until playback ends or the viewer answers. Never proceeds on its own."""

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self._series = kwargs.get("series", "")
        self._episode = kwargs.get("episode", "")
        self._thumb = kwargs.get("thumb", "")
        self._bar = None
        self.watch = False
        self._stop = False

    def onInit(self):
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

    def onClick(self, control_id):
        self.watch = control_id == _WATCH
        self._stop = True

    def onAction(self, action):
        if action.getId() in _CLOSE_ACTIONS:
            self._stop = True

    def _update_progress(self, player, total, span):
        if not (self._bar and total and span):
            return
        try:
            remaining = total - player.getTime()
            self._bar.setPercent(max(0.0, min(100.0, remaining * 100.0 / span)))
        except Exception:
            pass

    def run(self, player, monitor):
        """Block until answered or playback ends. True means play the next episode."""
        self.show()
        try:
            total = player.getTotalTime()
            span = max(1.0, total - player.getTime())
        except Exception:
            total, span = 0.0, 0.0

        try:
            while not self._stop:
                self._update_progress(player, total, span)
                if not player.isPlaying() or monitor.waitForAbort(0.2):
                    break
        finally:
            self.close()
        return self.watch


def ask_next_episode(series, episode, thumb, player, monitor):
    """Show the card. True if the viewer chose Watch now."""
    addon_path = xbmcvfs.translatePath(
        xbmcaddon.Addon(ADDON_ID).getAddonInfo("path")
    )
    card = NextEpisodeCard(
        "next_episode.xml", addon_path, "Default", "1080i",
        series=series, episode=episode, thumb=thumb,
    )
    try:
        return card.run(player, monitor)
    finally:
        del card
