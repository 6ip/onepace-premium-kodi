import xbmcaddon
import xbmcgui

ELEMENTUM_ADDON_ID = "plugin.video.elementum"


def check_elementum():
    try:
        addon = xbmcaddon.Addon(ELEMENTUM_ADDON_ID)
    except Exception:
        xbmcgui.Dialog().notification(
            "One Pace Premium",
            "Elementum is not installed",
            xbmcgui.NOTIFICATION_ERROR, 5000, True,
        )
        return

    xbmcgui.Dialog().notification(
        "One Pace Premium",
        f"Elementum detected (v{addon.getAddonInfo('version')})",
        xbmcgui.NOTIFICATION_INFO, 5000, False,
    )


if __name__ == "__main__":
    check_elementum()
