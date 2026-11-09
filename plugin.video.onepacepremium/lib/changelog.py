"""What's New window, shown from the settings or once after an update."""
import os
import re

import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import ADDON_ID, ADDON_NAME, log

_CLOSE_ACTIONS = (9, 10, 92)
_SELECT_ACTIONS = (7, 100)
_DONATE_BUTTON = 11
_TOGGLE_BUTTON = 12
_BUTTONS = (10, 11, 12)
_BODY = 2000
# A version block starts at the margin; its own lines are indented or blank.
_VERSION_HEAD = re.compile(r"(?m)^\[B\]v")


class ChangelogDialog(xbmcgui.WindowXMLDialog):

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self._text = kwargs.get("text", "")
        self._full = kwargs.get("full_text", "")
        self._version = kwargs.get("version", "")
        self._icon_path = kwargs.get("icon_path", "")
        self._heading = kwargs.get("heading", "What's New")
        self._asking = kwargs.get("asking", True)
        self._list_icon = kwargs.get("list_icon", "")
        self._note_icon = kwargs.get("note_icon", "")
        self._showing_full = False
        self.donate = False

    def onInit(self):
        self.setProperty("pp.version", self._version)
        self.setProperty("pp.icon", self._icon_path)
        self.setProperty("pp.asking", "1" if self._asking else "")
        # Only What's New has a longer version of itself to offer.
        self.setProperty("pp.toggle", "1" if self._full else "")
        self._render()
        # The scrollbar hides itself when the text fits, so focus a button.
        self.setFocusId(10)

    def _render(self):
        """Show whichever of the two texts is current, from the top."""
        full = self._showing_full
        self.setProperty("pp.text", self._full if full else self._text)
        self.setProperty("pp.heading", "Full Changelog" if full else self._heading)
        self.setProperty("pp.alticon", self._note_icon if full else self._list_icon)
        try:
            self.getControl(_BODY).scroll(0)
        except Exception:
            pass

    def onClick(self, control_id):
        # The corner button swaps the two texts instead of leaving.
        if control_id == _TOGGLE_BUTTON:
            self._showing_full = not self._showing_full
            self._render()
            return
        if control_id == _DONATE_BUTTON:
            self.donate = True
        self.close()

    def onAction(self, action):
        action_id = action.getId()
        # Enter closes too, unless a button has focus: that is its click.
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


def split_latest(text):
    """The newest version's block, and whether anything older follows it."""
    heads = [m.start() for m in _VERSION_HEAD.finditer(text)]
    if len(heads) < 2:
        return text, False
    return text[heads[0]:heads[1]].rstrip(), True


def show_text(text, version, heading="What's New", asking=True, full_text=""):
    """Open the What's New window on any text, and honour its donate button."""
    addon = xbmcaddon.Addon(ADDON_ID)
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    media = os.path.join(addon_path, "resources", "skins", "Default", "media")
    dialog = ChangelogDialog(
        "changelog.xml", addon_path, "Default", "1080i",
        text=text, version=version, heading=heading, asking=asking,
        full_text=full_text,
        icon_path=xbmcvfs.translatePath(addon.getAddonInfo("icon")),
        list_icon=os.path.join(media, "lists2.png"),
        note_icon=os.path.join(media, "sticky-note.png"),
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


def show_changelog(_params=None):
    addon = xbmcaddon.Addon(ADDON_ID)
    text = _read_changelog(xbmcvfs.translatePath(addon.getAddonInfo("path")))
    if not text:
        xbmcgui.Dialog().notification(
            ADDON_NAME, "No changelog found",
            xbmcgui.NOTIFICATION_ERROR, 4000, False,
        )
        return

    latest, more = split_latest(text)
    # A first release has nothing behind it, and then the button is pointless.
    show_text(latest, f"v{addon.getAddonInfo('version')}",
              full_text=text if more else "")


def maybe_show_on_update():
    """Show it once per version, without getting in the way if anything fails."""
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        version = addon.getAddonInfo("version")
        if addon.getSetting("last_seen_version") == version:
            return False
        addon.setSetting("last_seen_version", version)
        log(f"[changelog] first run of v{version}, showing what's new")
        show_changelog()
        return True
    except Exception as exc:
        log(f"[changelog] skipped on-update display: {exc}")
    return False
