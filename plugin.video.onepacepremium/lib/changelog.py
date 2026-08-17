"""What's New window, shown from the settings or once after an update."""
import os

import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import ADDON_ID, log

_CLOSE_ACTIONS = (9, 10, 92)
_SELECT_ACTIONS = (7, 100)
_DONATE_BUTTON = 11
_BUTTONS = (10, 11)


class ChangelogDialog(xbmcgui.WindowXMLDialog):

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self._text = kwargs.get("text", "")
        self._version = kwargs.get("version", "")
        self._icon_path = kwargs.get("icon_path", "")
        self.donate = False

    def onInit(self):
        self.setProperty("pp.text", self._text)
        self.setProperty("pp.version", self._version)
        self.setProperty("pp.icon", self._icon_path)
        # Focus the scrollbar so up and down scroll straight away.
        self.setFocusId(2060)

    def onClick(self, control_id):
        if control_id == _DONATE_BUTTON:
            self.donate = True
        self.close()

    def onAction(self, action):
        action_id = action.getId()
        # Enter closes too, but not while a button has focus — that is its click.
        if action_id in _CLOSE_ACTIONS or (
                action_id in _SELECT_ACTIONS and self.getFocusId() not in _BUTTONS):
            self.close()


def _read_changelog(addon_path):
    path = os.path.join(addon_path, "changelog.txt")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError as exc:
        log(f"[changelog] could not read {path}: {exc}")
        return ""


def show_changelog(_params=None):
    addon = xbmcaddon.Addon(ADDON_ID)
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    text = _read_changelog(addon_path)
    if not text:
        xbmcgui.Dialog().notification(
            "One Pace Premium", "No changelog found",
            xbmcgui.NOTIFICATION_ERROR, 4000, False,
        )
        return

    dialog = ChangelogDialog(
        "changelog.xml", addon_path, "Default", "1080i",
        text=text,
        version=f"v{addon.getAddonInfo('version')}",
        icon_path=xbmcvfs.translatePath(addon.getAddonInfo("icon")),
    )
    donate = False
    try:
        dialog.doModal()
        donate = dialog.donate
    finally:
        del dialog

    if donate:
        from .donate import show_donate
        show_donate()


def maybe_show_on_update():
    """Show it once per version, without getting in the way if anything fails."""
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        version = addon.getAddonInfo("version")
        if addon.getSetting("last_seen_version") == version:
            return
        addon.setSetting("last_seen_version", version)
        log(f"[changelog] first run of v{version}, showing what's new")
        show_changelog()
    except Exception as exc:
        log(f"[changelog] skipped on-update display: {exc}")
