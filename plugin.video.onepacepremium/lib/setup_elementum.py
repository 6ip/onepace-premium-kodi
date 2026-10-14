import xbmcaddon
import xbmcgui

ELEMENTUM_ADDON_ID = "plugin.video.elementum"
# Launched with RunScript, so it carries its own copy of the name.
ADDON_NAME = "One Pace Addon"


def check_elementum():
    try:
        addon = xbmcaddon.Addon(ELEMENTUM_ADDON_ID)
    except Exception:
        xbmcgui.Dialog().notification(
            ADDON_NAME,
            "Elementum is not installed",
            xbmcgui.NOTIFICATION_ERROR, 5000, True,
        )
        return

    xbmcgui.Dialog().notification(
        ADDON_NAME,
        f"Elementum detected (v{addon.getAddonInfo('version')})",
        xbmcgui.NOTIFICATION_INFO, 5000, False,
    )


if __name__ == "__main__":
    check_elementum()
