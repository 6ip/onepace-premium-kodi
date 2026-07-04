import os
import struct
import sys
import threading
import time
import zlib

import requests
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "plugin.video.onepacepremium"
_POLL_INTERVAL = 3  # seconds between server checks

_VENDOR = os.path.join(os.path.dirname(__file__), "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

try:
    import segno
    _SEGNO_OK = True
except Exception:
    _SEGNO_OK = False


# ── QR rendering ────────────────────────────────────────────────────────────

def _encode_png_rgba(rgba: bytes, width: int, height: int) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = chk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride:(y + 1) * stride])
    idat = chk(b"IDAT", zlib.compress(bytes(raw), 6))
    iend = chk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _render_rounded_qr(
    matrix,
    scale: int = 10,
    border: int = 2,
    module_radius_ratio: float = 0.0,
    bg_rgba: tuple = (255, 255, 255, 255),
    fg_rgba: tuple = (0, 0, 0, 255),
) -> bytes:
    """Render QR matrix to RGBA PNG with rounded outer corners (NuvioTV style)."""
    n = len(matrix)
    img_size = (n + 2 * border) * scale

    bg = bytes(bg_rgba)
    buf = bytearray(bg * img_size * img_size)

    # Clip outer shape to rounded rect (6% corner radius)
    cr = img_size * 0.06
    icr = int(cr) + 2
    corner_zones = [
        (0,              icr, 0,              icr),
        (img_size - icr, img_size, 0,         icr),
        (0,              icr, img_size - icr,  img_size),
        (img_size - icr, img_size, img_size - icr, img_size),
    ]
    arc_cx = [cr, img_size - 1 - cr, cr, img_size - 1 - cr]
    arc_cy = [cr, cr, img_size - 1 - cr, img_size - 1 - cr]

    for zone_idx, (x0, x1, y0, y1) in enumerate(corner_zones):
        cx, cy = arc_cx[zone_idx], arc_cy[zone_idx]
        for py in range(y0, y1):
            for px in range(x0, x1):
                if (px - cx) ** 2 + (py - cy) ** 2 > cr ** 2:
                    buf[(py * img_size + px) * 4 + 3] = 0

    # Draw modules in fg_rgba
    fr, fg, fb, fa = fg_rgba
    offset = border * scale
    half = scale / 2.0
    r = scale * module_radius_ratio
    inner = half - r

    for row_i, row in enumerate(matrix):
        for col_i, is_dark in enumerate(row):
            if not is_dark:
                continue
            base_x = offset + col_i * scale
            base_y = offset + row_i * scale
            for py in range(base_y, base_y + scale):
                for px in range(base_x, base_x + scale):
                    lx, ly = px - base_x, py - base_y
                    dx = abs(lx - half + 0.5)
                    dy = abs(ly - half + 0.5)
                    if dx <= inner or dy <= inner:
                        draw = dx < half and dy < half
                    else:
                        draw = (dx - inner) ** 2 + (dy - inner) ** 2 <= r ** 2
                    if draw:
                        i = (py * img_size + px) * 4
                        buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = fr, fg, fb, fa

    return _encode_png_rgba(bytes(buf), img_size, img_size)


def _save_qr(url: str, path: str) -> bool:
    if not _SEGNO_OK:
        return False
    try:
        qr = segno.make_qr(url, error="M")
        matrix = list(qr.matrix)
        png = _render_rounded_qr(matrix, scale=10, border=2, module_radius_ratio=0.0)
        with open(path, "wb") as f:
            f.write(png)
        return True
    except Exception as exc:
        xbmc.log(f"[OnePace] QR generation failed: {exc}", xbmc.LOGWARNING)
        return False


# ── Result object ────────────────────────────────────────────────────────────

class DialogResult:
    """Returned by show_setup_dialog() to describe why the dialog closed."""
    __slots__ = ("open_browser", "setup_complete", "expired", "manifest_data")

    def __init__(self):
        self.open_browser = False      # user pressed "Open Browser"
        self.setup_complete = False    # server confirmed setup while dialog was open
        self.expired = False           # code expired while dialog was open
        self.manifest_data = {}        # server payload when setup_complete is True


# ── Dialog ──────────────────────────────────────────────────────────────────

class SetupDialog(xbmcgui.WindowXMLDialog):

    def __init__(self, xml_file, addon_path, default_skin, default_res, **kwargs):
        super().__init__(xml_file, addon_path, default_skin, default_res)
        self._code = kwargs["code"]
        self._color = kwargs["color"].upper().lstrip("#")
        self._qr_path = kwargs.get("qr_path", "")
        self._icon_path = kwargs.get("icon_path", "")
        self._poll_url = kwargs.get("poll_url", "")
        self._expires_in = kwargs.get("expires_in", 0)

        self.result = DialogResult()
        self._stop = False

    def onInit(self):
        self.setProperty("pp.code", self._code)
        self.setProperty("pp.codeColor", f"FF{self._color}")
        self.setProperty("pp.icon", self._icon_path)
        if self._qr_path:
            self.getControl(200).setImage(self._qr_path)
        self.setFocusId(11)

        # Start background polling so the dialog auto-closes when setup finishes
        if self._poll_url and self._expires_in > 0:
            t = threading.Thread(target=self._poll_loop, daemon=True)
            t.start()

    def _poll_loop(self):
        deadline = time.time() + self._expires_in
        while not self._stop and time.time() < deadline:
            time.sleep(_POLL_INTERVAL)
            if self._stop:
                break
            try:
                resp = requests.get(self._poll_url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue

            if data.get("status") == "pending":
                continue

            if "secret_string" in data:
                self.result.setup_complete = True
                self.result.manifest_data = data
                self._stop = True
                return  # run() loop on main thread calls close()

        # Only mark expired if the dialog wasn't closed by the user
        if not self._stop:
            self.result.expired = True
            self._stop = True
            # run() loop on main thread calls close()

    def _user_close(self):
        """Called by Kodi's event dispatch — safe to call close() here directly."""
        self._stop = True
        self.close()

    def onClick(self, control_id):
        if control_id == 10:
            self.result.open_browser = True
        self._user_close()

    def onAction(self, action):
        if action.getId() in (9, 10, 92):
            self._user_close()

    def run(self) -> DialogResult:
        # Use show() so this thread stays free to drive the close() call.
        # WindowXMLDialog is always modal (rendered on top) regardless of show vs doModal.
        # Calling close() from a background thread via doModal() is unreliable in Kodi;
        # this pattern lets the background thread set _stop and the main thread closes.
        self.show()
        while not self._stop:
            xbmc.sleep(100)
        self.close()
        return self.result


# ── Public entry point ───────────────────────────────────────────────────────

def show_setup_dialog(
    code: str,
    color: str,
    configure_url: str,
    profile_dir: str,
    poll_url: str = "",
    expires_in: float = 0,
) -> DialogResult:
    """Show the setup dialog with an embedded QR code.

    Polls the server in the background while the dialog is visible.
    Returns a DialogResult describing why the dialog closed.
    """
    qr_path = os.path.join(profile_dir, "setup_qr.png")
    if not _save_qr(configure_url, qr_path):
        qr_path = ""

    addon = xbmcaddon.Addon(ADDON_ID)
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    icon_path = os.path.join(addon_path, "resources", "icon.png")

    dialog = SetupDialog(
        "setup_dialog.xml",
        addon_path,
        "Default",
        "1080i",
        code=code,
        color=color,
        qr_path=qr_path,
        icon_path=icon_path,
        poll_url=poll_url,
        expires_in=expires_in,
    )
    result = dialog.run()
    del dialog

    if qr_path and os.path.isfile(qr_path):
        try:
            os.remove(qr_path)
        except OSError:
            pass

    return result
