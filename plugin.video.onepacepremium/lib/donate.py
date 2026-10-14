"""Support the Project window, with a QR to the donation page."""
import glob
import hashlib
import os

import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import ADDON_ID, log

DONATE_URL = "https://onepace.e6ip.com/donate"
# The address is in the filename, so a new one renders a new picture rather
# than serving the old QR under the new link.
_QR_FILE = f"donate_qr_{hashlib.sha1(DONATE_URL.encode()).hexdigest()[:8]}.png"

_OPEN_BUTTON = 10
_CLOSE_BUTTON = 11
_CLOSE_ACTIONS = (9, 10, 92)


class DonateDialog(xbmcgui.WindowXMLDialog):

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self._qr_path = kwargs.get("qr_path", "")
        self._icon_path = kwargs.get("icon_path", "")
        self.open_page = False

    def onInit(self):
        self.setProperty("pp.icon", self._icon_path)
        self.setProperty("pp.url", DONATE_URL.split("//", 1)[-1])
        if self._qr_path:
            try:
                self.getControl(200).setImage(self._qr_path)
            except Exception:
                pass
        self.setFocusId(_OPEN_BUTTON)

    def onClick(self, control_id):
        if control_id == _OPEN_BUTTON:
            self.open_page = True
        self.close()

    def onAction(self, action):
        if action.getId() in _CLOSE_ACTIONS:
            self.close()


def _profile():
    path = xbmcvfs.translatePath(
        xbmcaddon.Addon(ADDON_ID).getAddonInfo("profile")
    )
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path if path.endswith(("/", "\\")) else path + "/"


def _qr_path():
    """Render the QR once and keep it in the profile."""
    path = _profile() + _QR_FILE
    if os.path.exists(path):
        return path
    for stale in glob.glob(_profile() + "donate_qr*.png"):
        try:
            os.remove(stale)
        except OSError:
            pass
    from .setup_dialog import _save_qr
    return path if _save_qr(DONATE_URL, path) else ""


def show_donate(_params=None):
    addon = xbmcaddon.Addon(ADDON_ID)
    dialog = DonateDialog(
        "donate.xml",
        xbmcvfs.translatePath(addon.getAddonInfo("path")),
        "Default", "1080i",
        qr_path=_qr_path(),
        icon_path=xbmcvfs.translatePath(addon.getAddonInfo("icon")),
    )
    open_page = False
    try:
        dialog.doModal()
        open_page = dialog.open_page
    finally:
        del dialog

    if open_page:
        from .custom_settings_window import open_configuration_page
        try:
            open_configuration_page(DONATE_URL)
        except Exception as exc:
            log(f"[donate] could not open {DONATE_URL}: {exc}")
            xbmcgui.Dialog().ok("Support the Project",
                                f"Open this link on your phone or browser:\n\n{DONATE_URL}")
