"""The Downloads manager: what is transferring, and a way to stop it."""
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import download_queue
from .utils import ADDON_ID, log

_LIST = 2500
_CLOSE_BUTTON = 10
_CANCEL_ALL = 11
_CLOSE_ACTIONS = (9, 10, 92)


class DownloadsManager(xbmcgui.WindowXMLDialog):
    """The transfer in flight redraws itself from window properties.

    Nothing here runs on a timer. A control may only be touched from the
    thread Kodi calls us on, so the moving parts are left to the skin and
    the list is rebuilt on a keypress, and only when it has actually changed.
    """

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self.shown = []

    def onInit(self):
        download_queue.publish()
        self._draw(first=True)

    def onAction(self, action):
        if action.getId() in _CLOSE_ACTIONS:
            self.close()
            return
        self._sync()

    def onClick(self, control_id):
        if control_id == _CLOSE_BUTTON:
            self.close()
        elif control_id == _CANCEL_ALL:
            self._cancel_all()
        elif control_id == _LIST:
            self._cancel_selected()

    def _sync(self):
        """Catch the list up, but only when the queue is not what we drew."""
        if [r["ticket"] for r in download_queue.snapshot()] != self.shown:
            self._draw()

    def _draw(self, first=False):
        rows = download_queue.snapshot()
        self.shown = [r["ticket"] for r in rows]
        control = self.getControl(_LIST)
        position = 0 if first else control.getSelectedPosition()
        control.reset()
        items = []
        for row in rows:
            item = xbmcgui.ListItem(offscreen=True)
            item.setProperties({
                "name": row["name"], "detail": row["detail"],
                "percent": str(row["pct"]), "status": row["status"],
                "ticket": row["ticket"],
            })
            items.append(item)
        control.addItems(items)
        if not rows:
            self.setFocusId(_CLOSE_BUTTON)
        elif first:
            self.setFocusId(_LIST)
        else:
            control.selectItem(min(max(position, 0), len(rows) - 1))

    def _cancel_selected(self):
        try:
            item = self.getControl(_LIST).getSelectedItem()
        except Exception:
            return
        ticket = item.getProperty("ticket") if item else ""
        if not ticket:
            return
        if xbmcgui.Dialog().yesno("Cancel Download",
                                  f"Stop {item.getProperty('name')}?",
                                  nolabel="Keep going", yeslabel="Stop"):
            download_queue.request_cancel([ticket])
            self._draw()

    def _cancel_all(self):
        tickets = [row["ticket"] for row in download_queue.snapshot() if row["ticket"]]
        if not tickets:
            return
        if xbmcgui.Dialog().yesno("Cancel Downloads",
                                  f"Stop all {len(tickets)} of them?",
                                  nolabel="Keep going", yeslabel="Stop all"):
            download_queue.request_cancel(tickets)
            self._draw()


def show(_params=None):
    """Open the manager. Called from the Downloads menu."""
    addon = xbmcaddon.Addon(ADDON_ID)
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    window = DownloadsManager("downloads_manager.xml", addon_path, "Default", "1080i")
    try:
        window.doModal()
    except Exception as exc:
        log(f"[downloads] the manager window failed: {exc}")
    finally:
        del window
