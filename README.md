# 🎌 One Pace Premium — Kodi Add-on

Kodi plugin (`plugin.video.onepacepremium`) and its update repository (`repository.onepacepremium`). Watch the complete One Pace catalog with your debrid service directly inside Kodi.

## 📋 Prerequisites

**For torrent/P2P streams** (optional — debrid streams work without this):

1. Go to **https://elementum.surge.sh/** and download the **Universal** version under *Elementum Downloads*
2. In Kodi: **Add-ons** ➔ **Install from zip file** ➔ select the downloaded zip
3. Elementum is now installed and ready for torrent playback

> Debrid streams work without Elementum.

## 🚀 Installation (Recommended)

Using the repository ensures you receive automatic updates.

1. **Add Source**: Go to **Settings** ➔ **File manager** ➔ **Add source**.
2. **Enter URL**: Enter `https://6ip.github.io/onepace-premium-kodi` and name it `One Pace Premium`.
3. **Install Repository**: Go to **Add-ons** ➔ **Install from zip file** ➔ select `One Pace Premium` ➔ install `repository.onepacepremium-X.Y.Z.zip`.
4. **Install Add-on**: Go to **Install from repository** ➔ **One Pace Premium Repository** ➔ **Video add-ons** ➔ **One Pace Premium** ➔ **Install**.

> If step 4 fails right after installing the repository, restart Kodi and try again.

## ⚙️ Configuration

Once installed, link the add-on to your account:

1. Go to **Add-ons** ➔ **My add-ons** ➔ **Video add-ons** ➔ **One Pace Premium** ➔ **Configure**.
2. Click **Configure/Reconfigure**.
3. An **8-character hex setup code** will appear (e.g., `1a2b3c4d`).
4. Open the configuration page on your phone or browser — the URL is shown on screen.
5. Select your debrid provider and paste your API key.
6. Click **Install Addon** — Kodi detects the setup automatically.

> Alternatively, open the configuration page manually, click **Setup Kodi** from the install menu, and enter the code shown in Kodi.

## 📦 Manual Installation

*You will not receive automatic updates with this method.*

1. Download the latest plugin zip from the [One Pace Premium Kodi Repository](https://6ip.github.io/onepace-premium-kodi/).
2. Go to **Add-ons** ➔ **Install from zip file** ➔ select the downloaded zip.
3. Follow the **Configuration** steps above.

---

## 🛠️ Development & Building

```sh
make          # Full build: add-on + repository
make package  # Add-on zip only
```

### Build Outputs (`dist/`)
```
dist/
├── addons.xml + addons.xml.md5
├── plugin.video.onepacepremium/
│   ├── addon.xml
│   └── plugin.video.onepacepremium-X.Y.Z.zip
├── repository.onepacepremium/
│   ├── addon.xml
│   └── repository.onepacepremium-X.Y.Z.zip
└── index.html
```
