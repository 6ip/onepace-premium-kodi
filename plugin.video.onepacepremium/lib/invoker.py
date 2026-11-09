"""Reuse Language Invoker: faster menus, at a risk only Kodi can remove."""
import re

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import ADDON_ID, ADDON_NAME, get_setting, log

_MANIFEST = f"special://home/addons/{ADDON_ID}/addon.xml"
_REUSE = re.compile(r"(<reuselanguageinvoker>)(\w*)(</reuselanguageinvoker>)")
_TITLE = "Reuse Language Invoker"


def _manifest():
    return xbmcvfs.translatePath(_MANIFEST)


def _read():
    """What addon.xml says today, or "" when it cannot be read."""
    try:
        with xbmcvfs.File(_manifest()) as handle:
            found = _REUSE.search(handle.read() or "")
        return found.group(2) if found else ""
    except Exception as exc:
        log(f"[invoker] could not read the manifest: {exc}")
        return ""


def _write(value):
    """Set it, then read it back: some systems keep the folder read-only."""
    try:
        with xbmcvfs.File(_manifest()) as handle:
            source = handle.read() or ""
        if not _REUSE.search(source):
            return False
        with xbmcvfs.File(_manifest(), "w") as handle:
            handle.write(_REUSE.sub(rf"\g<1>{value}\g<3>", source))
    except Exception as exc:
        log(f"[invoker] could not write the manifest: {exc}")
        return False
    return _read() == value


def _warning():
    return ("Turning this on can make Kodi close unexpectedly during playback."
            + chr(10) + chr(10) + "Nothing in this add-on can prevent it.")


def _apply(want):
    """Write it where Kodi reads it. True when a profile reload was started."""
    if not _write(want):
        xbmcgui.Dialog().ok(
            _TITLE,
            "Could not write to the add-on's own folder." + chr(10) + chr(10)
            + "Some systems keep it read-only, and then this cannot be changed.")
        return False
    addon = xbmcaddon.Addon(ADDON_ID)
    addon.setSetting("invoker_reuse", want)
    addon.setSetting("invoker_display", "On" if want == "true" else "Off")
    log(f"[invoker] reuse is now {want!r}")

    if xbmcgui.Dialog().yesno(
            _TITLE,
            f"[B]{_TITLE}[/B] is now [B]{'on' if want == 'true' else 'off'}[/B]."
            + chr(10) + chr(10)
            + "Kodi has to reload your profile before it takes effect.",
            nolabel="Later", yeslabel="Reload now"):
        xbmc.executebuiltin(
            f"LoadProfile({xbmc.getInfoLabel('System.ProfileName')})")
        return True
    return False


def _change():
    """The question, the warning, and the write. True if Kodi is reloading."""
    now = _read()
    if not now:
        xbmcgui.Dialog().ok(ADDON_NAME,
                            "Could not read the add-on's own addon.xml.")
        return False
    on = now == "true"
    if not xbmcgui.Dialog().yesno(
            _TITLE,
            f"[B]{_TITLE}[/B] is currently [B]{'on' if on else 'off'}[/B]."
            + chr(10) + chr(10)
            + "Menus and the return from an episode come back quicker with it on."
            + chr(10) + chr(10)
            + f"Change it to [B]{'off' if on else 'on'}[/B]?",
            nolabel="Leave it", yeslabel="Change it"):
        return False
    if not on and not xbmcgui.Dialog().yesno(
            _TITLE, _warning(),
            nolabel="Cancel", yeslabel="Turn it on anyway"):
        return False
    return _apply("false" if on else "true")


def choose(_params=None):
    """The settings row: ask, then put the viewer back where they were."""
    reloaded = False
    try:
        reloaded = _change()
    finally:
        if not reloaded:
            xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")


def maybe_offer():
    """Once, on the first browse after installing or updating to this version."""
    addon = xbmcaddon.Addon(ADDON_ID)
    if addon.getSetting("invoker_asked") == "true":
        return
    # Written first: a force quit mid-dialog must not mean asking forever.
    addon.setSetting("invoker_asked", "true")
    if _read() != "false":
        return
    if not xbmcgui.Dialog().yesno(
            _TITLE,
            f"Menus and the return from an episode come back quicker with "
            f"[B]{_TITLE}[/B] on." + chr(10) + chr(10)
            + "You can change this later under Tools.",
            nolabel="Not now", yeslabel="Turn it on"):
        log("[invoker] offered once, and left off")
        return
    if not xbmcgui.Dialog().yesno(_TITLE, _warning(),
                                  nolabel="Cancel", yeslabel="Turn it on anyway"):
        return
    _apply("true")


def reapply():
    """An update replaces the manifest, so put the viewer's choice back."""
    wanted = get_setting("invoker_reuse") or "false"
    if wanted == "false" or _read() in ("", wanted):
        return False
    if _write(wanted):
        log(f"[invoker] put {wanted!r} back after the manifest was replaced")
        return True
    return False
