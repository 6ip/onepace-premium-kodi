import json
import os
import random
import subprocess
import time
import traceback
from urllib.parse import urljoin

import requests
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "plugin.video.onepacepremium"
REQUEST_TIMEOUT = 20
POLL_INTERVAL_SECONDS = 3
HTTP_SESSION = requests.Session()
PENDING_SETUP_FILE = "pending_setup.json"
CODE_COLORS = [
    "ffffff",
    "66BB6A",
    "A7F3D0",
    "60A5FA",
    "80D8FF",
    "E9D5FF",
    "FF9100",
    "FF6B35",
    "FFC107",
    "FBBF24",
    "ccccff",
    "ff9966",
    "ff9999",
]


def normalize_base_url(url: str):
    return url.rstrip("/")


def open_configuration_page(url: str):
    os_windows = xbmc.getCondVisibility("system.platform.windows")
    os_osx = xbmc.getCondVisibility("system.platform.osx")
    os_linux = xbmc.getCondVisibility("system.platform.linux")
    os_android = xbmc.getCondVisibility("System.Platform.Android")

    try:
        if os_osx:
            subprocess.run(["open", url], check=True)
            return
        if os_windows:
            os.startfile(url)
            return
        if os_linux and not os_android:
            subprocess.run(["xdg-open", url], check=True)
            return
        if os_android:
            safe_url = url.replace('"', "%22")
            xbmc.executebuiltin(
                f'StartAndroidActivity("","android.intent.action.VIEW","","{safe_url}")'
            )
            return
    except Exception as exc:
        xbmc.log(f"Failed to open configuration page: {exc}", xbmc.LOGERROR)


def _post_json(url: str, payload: dict):
    response = HTTP_SESSION.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _get_json(url: str):
    response = HTTP_SESSION.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _pending_setup_path(addon):
    profile_dir = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    if not os.path.isdir(profile_dir):
        os.makedirs(profile_dir, exist_ok=True)
    return os.path.join(profile_dir, PENDING_SETUP_FILE)


def _load_pending_setup(addon):
    path = _pending_setup_path(addon)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_pending_setup(addon, code, configure_url, expires_at, base_url, color):
    with open(_pending_setup_path(addon), "w", encoding="utf-8") as f:
        json.dump(
            {
                "code": code,
                "configure_url": configure_url,
                "expires_at": expires_at,
                "base_url": base_url,
                "color": color,
            },
            f,
        )


def _clear_pending_setup(addon):
    path = _pending_setup_path(addon)
    if os.path.isfile(path):
        os.remove(path)


def configure_addon():
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        dialog = xbmcgui.Dialog()
        monitor = xbmc.Monitor()

        base_url = addon.getSetting("base_url")

        entered_url = dialog.input("One Pace Premium server URL", base_url)
        if not entered_url:
            return

        base_url = normalize_base_url(entered_url)
        addon.setSetting("base_url", base_url)

        # Reuse an existing, unexpired setup code instead of spamming a new one
        code = None
        configure_url = None
        expires_in = None
        color = None

        pending = _load_pending_setup(addon)
        if pending and pending.get("base_url") == base_url:
            remaining = pending.get("expires_at", 0) - time.time()
            if remaining > 0:
                code = pending["code"]
                configure_url = pending["configure_url"]
                expires_in = remaining
                color = pending.get("color")

        if code is None:
            try:
                data = _post_json(
                    urljoin(base_url + "/", "kodi/generate_setup_code"),
                    {},
                )
            except requests.RequestException as exc:
                dialog.notification(
                    "One Pace Premium",
                    "Failed to generate setup code",
                    xbmcgui.NOTIFICATION_ERROR,
                )
                xbmc.log(f"Failed to generate setup code: {exc}", xbmc.LOGERROR)
                return

            try:
                code = data["code"]
                configure_url = data["configure_url"]
                expires_in = data["expires_in"]
                stremio_api_prefix = data.get("stremio_api_prefix", "")
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError("Invalid response from /kodi/generate_setup_code") from exc

            addon.setSetting("stremio_api_prefix", stremio_api_prefix)
            color = random.choice(CODE_COLORS)
            _save_pending_setup(addon, code, configure_url, time.time() + expires_in, base_url, color)

        if not color:
            color = random.choice(CODE_COLORS)

        if dialog.yesno(
            "One Pace Premium Kodi Setup",
            f"Setup code: [COLOR FF{color}][B]{code}[/B][/COLOR]\nOpen the configuration page on your phone or browser and complete setup before the code expires.",
            yeslabel="Open Browser",
            nolabel="Got It",
        ):
            open_configuration_page(configure_url)

        dialog.notification(
            "One Pace Premium",
            f"Waiting for setup code {code}...",
            xbmcgui.NOTIFICATION_INFO,
        )

        deadline = time.time() + expires_in
        while time.time() < deadline:
            current_pending = _load_pending_setup(addon)
            if not current_pending or current_pending.get("code") != code:
                # Another poller already finished (or superseded) this code
                return

            try:
                manifest_data = _get_json(
                    urljoin(base_url + "/", f"kodi/get_manifest/{code}")
                )
            except requests.HTTPError as exc:
                response = exc.response
                if response is None or response.status_code not in (404, 202):
                    xbmc.log(f"Polling setup status failed: {exc}", xbmc.LOGWARNING)
            except requests.RequestException as exc:
                xbmc.log(f"Polling setup status failed: {exc}", xbmc.LOGWARNING)
            else:
                if manifest_data.get("status") == "pending":
                    pass  # still waiting
                elif "secret_string" in manifest_data:
                    addon.setSetting("secret_string", manifest_data["secret_string"])
                    if "stremio_api_prefix" in manifest_data:
                        addon.setSetting(
                            "stremio_api_prefix", manifest_data["stremio_api_prefix"]
                        )
                    _clear_pending_setup(addon)
                    dialog.notification(
                        "One Pace Premium",
                        "Kodi setup complete!",
                        xbmcgui.NOTIFICATION_INFO,
                    )
                    return

            if monitor.waitForAbort(POLL_INTERVAL_SECONDS):
                return

        pending = _load_pending_setup(addon)
        if pending and pending.get("code") == code:
            _clear_pending_setup(addon)
            dialog.notification(
                "One Pace Premium",
                "Setup code expired. Run setup again.",
                xbmcgui.NOTIFICATION_ERROR,
            )
    except Exception:
        xbmc.log(
            "One Pace Premium Kodi setup crashed:\n" + traceback.format_exc(),
            xbmc.LOGERROR,
        )
        xbmcgui.Dialog().notification(
            "One Pace Premium",
            "Setup failed (check Kodi log)",
            xbmcgui.NOTIFICATION_ERROR,
        )


if __name__ == "__main__":
    configure_addon()
