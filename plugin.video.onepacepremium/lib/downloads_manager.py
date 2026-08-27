"""The Downloads manager: what is transferring now, and a way to stop it."""
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import download_queue
from .utils import ADDON_ID, log

_LIST = 2500
_CLOSE_BUTTON = 10
_CANCEL_ALL = 11
_CLOSE_ACTIONS = (9, 10, 92)
# Slow enough that the window is not rebuilt under the viewer's cursor.
_REDRAW = 2.5


class DownloadsManager(xbmcgui.WindowXMLDialog):

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self.closed = False
        self.position = 0

    def onInit(self):
        self._draw(first=True)
        self._watch()

    def onAction(self, action):
        if action.getId() in _CLOSE_ACTIONS:
            self.closed = True
            self.close()

    def onClick(self, control_id):
        if control_id in (_CLOSE_BUTTON,):
            self.closed = True
            self.close()
        elif control_id == _CANCEL_ALL:
            self._cancel_all()
        elif control_id == _LIST:
            self._cancel_selected()

    def _rows_now(self):
        return download_queue.snapshot()

    def _draw(self, first=False):
        rows = self._rows_now()
        control = self.getControl(_LIST)
        if not first:
            self.position = control.getSelectedPosition()
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
        self.setProperty("pp.count", str(len(rows)))
        self.setProperty("pp.many", "true" if len(rows) > 1 else "")
        if not rows:
            self.setFocusId(_CLOSE_BUTTON)
        elif first:
            self.setFocusId(_LIST)
        else:
            control.selectItem(min(self.position, len(rows) - 1))

    def _selected_ticket(self):
        try:
            return self.getControl(_LIST).getSelectedItem().getProperty("ticket")
        except Exception:
            return ""

    def _cancel_selected(self):
        ticket = self._selected_ticket()
        if not ticket:
            return
        name = self.getControl(_LIST).getSelectedItem().getProperty("name")
        if xbmcgui.Dialog().yesno("Cancel Download", f"Stop {name}?",
                                  nolabel="Keep going", yeslabel="Stop"):
            download_queue.request_cancel([ticket])
            self._draw()

    def _cancel_all(self):
        tickets = [row["ticket"] for row in self._rows_now() if row["ticket"]]
        if not tickets:
            return
        if xbmcgui.Dialog().yesno("Cancel Downloads",
                                  f"Stop all {len(tickets)} of them?",
                                  nolabel="Keep going", yeslabel="Stop all"):
            download_queue.request_cancel(tickets)
            self._draw()

    def _watch(self):
        """Redraw while anything is moving.

        Nothing new can start behind a modal window, so once the queue is
        empty there is nothing left to watch for and the loop ends rather
        than idling for as long as the window stays open.
        """
        monitor = xbmc.Monitor()
        while not self.closed and download_queue.busy():
            if monitor.waitForAbort(_REDRAW):
                break
            if self.closed:
                return
            self._draw()
        if not self.closed:
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
