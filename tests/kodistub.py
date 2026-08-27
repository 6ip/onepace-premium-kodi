"""Stand-in Kodi modules, so the add-on can be imported outside Kodi.

Only the surface the add-on actually touches is modelled. Anything a test needs
to observe (art, properties, dialog answers) is recorded rather than discarded.
"""
import sys
import types

ADDON_ROOT = None       # set by install(); the add-on directory
FIXTURES = None         # set by install(); tests/fixtures


# Kodi keeps window properties on the window itself, so every invocation
# that asks for the same id sees the same ones. They die with the session.
_WINDOW_PROPS = {}


class Recorder:
    """Collects what the add-on did, so a test can assert on it."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.directories = []      # (url, listitem, is_folder)
        self.ended = []            # succeeded flags
        self.notifications = []    # (message, "INFO" | "ERROR")
        self.builtins = []         # executebuiltin strings
        self.resolved = []         # setResolvedUrl listitems
        self.content = None
        self.infolabels = {}       # Container.* and friends
        self.progress = []         # DialogProgressBG calls
        self.rmdirs = []           # folders we asked Kodi to remove
        _WINDOW_PROPS.clear()


recorder = Recorder()


class _Tag:
    """Video info tag. Records every setter under its own name."""

    def __init__(self):
        self.calls = {}

    def __getattr__(self, name):
        def record(*args, **kwargs):
            if kwargs and not args:
                self.calls[name] = kwargs
            else:
                self.calls[name] = args[0] if len(args) == 1 else args
        return record


class ListItem:
    def __init__(self, label="", label2="", path="", offscreen=False):
        self.label, self.path = label, path
        self.art, self.properties, self.context = {}, {}, []
        self.subtitles = []
        self._tag = _Tag()

    def getVideoInfoTag(self):
        return self._tag

    def setArt(self, art):
        self.art.update(art)

    def setProperty(self, key, value):
        self.properties[key] = value

    def setProperties(self, props):
        self.properties.update(props)

    def addContextMenuItems(self, items, replaceItems=False):
        self.context = list(items)

    def setSubtitles(self, subs):
        self.subtitles = list(subs)

    def setLabel(self, label):
        self.label = label

    def setPath(self, path):
        self.path = path

    def setInfo(self, *a, **k):
        pass

    def setContentLookup(self, *a, **k):
        pass

    def setIsFolder(self, *a, **k):
        pass


class Dialog:
    """Answers come from the queues a test fills in before acting."""

    select_answers = []
    multiselect_answers = []
    yesno_answers = []
    browse_answers = []
    inputs = []

    def notification(self, heading, message, icon=None, time=0, sound=True):
        kind = "ERROR" if icon == NOTIFICATION_ERROR else "INFO"
        recorder.notifications.append((message, kind))

    def ok(self, heading, message, *a, **k):
        recorder.notifications.append((message, "OK"))

    def select(self, heading, options, **k):
        return Dialog.select_answers.pop(0) if Dialog.select_answers else -1

    def multiselect(self, heading, options, preselect=None, **k):
        return Dialog.multiselect_answers.pop(0) if Dialog.multiselect_answers else None

    def yesno(self, heading, message, **k):
        return Dialog.yesno_answers.pop(0) if Dialog.yesno_answers else False

    def yesnocustom(self, *a, **k):
        return Dialog.yesno_answers.pop(0) if Dialog.yesno_answers else -1

    def browseSingle(self, *a, **k):
        return Dialog.browse_answers.pop(0) if Dialog.browse_answers else ""

    def input(self, *a, **k):
        return Dialog.inputs.pop(0) if Dialog.inputs else ""


class Window:
    def __init__(self, window_id=0):
        self.props = _WINDOW_PROPS.setdefault(window_id, {})

    def setProperty(self, k, v):
        self.props[k] = v

    def clearProperty(self, k):
        self.props.pop(k, None)

    def getProperty(self, k):
        return self.props.get(k, "")


class WindowXMLDialog:
    def __init__(self, *a, **k):
        pass


class Actor:
    def __init__(self, name="", role="", order=0, thumbnail=""):
        self.name, self.role, self.order, self.thumbnail = name, role, order, thumbnail


class Monitor:
    def waitForAbort(self, secs=0):
        return False

    def abortRequested(self):
        return False


class Player:
    def isPlaying(self):
        return False


NOTIFICATION_INFO, NOTIFICATION_ERROR, NOTIFICATION_WARNING = 0, 1, 2
INPUT_ALPHANUM = 0


def install(addon_root, fixtures, settings=None):
    """Put the fake Kodi modules on sys.modules and the add-on on sys.path."""
    global ADDON_ROOT, FIXTURES
    ADDON_ROOT, FIXTURES = str(addon_root), str(fixtures)
    store = dict(settings or {})

    def module(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    class Addon:
        def __init__(self, *a):
            pass

        def getAddonInfo(self, key):
            return {"id": "plugin.video.onepacepremium", "path": ADDON_ROOT,
                    "profile": FIXTURES + "/", "version": "0.0.0",
                    "icon": ADDON_ROOT + "/resources/icon.png"}.get(key, "")

        def getSetting(self, key):
            return store.get(key, "")

        def setSetting(self, key, value):
            store[key] = value

    def _builtin(command):
        recorder.builtins.append(command)

    module("xbmc", log=lambda *a, **k: None, LOGINFO=0, LOGWARNING=1, LOGERROR=2,
           translatePath=lambda p: p, executebuiltin=_builtin,
           getInfoLabel=lambda k: recorder.infolabels.get(k, ""),
           getCondVisibility=lambda k: False,
           getLanguage=lambda *a, **k: "eng", ISO_639_2=1,
           Monitor=Monitor, Player=Player, Actor=Actor, sleep=lambda ms: None)
    module("xbmcaddon", Addon=Addon)
    class DialogProgressBG:
        """Records the headings it was given, and never blocks."""

        def create(self, heading="", message=""):
            recorder.progress.append(("create", heading, message))

        def update(self, percent=0, heading="", message=""):
            recorder.progress.append(("update", percent, message))

        def close(self):
            recorder.progress.append(("close",))

    module("xbmcgui", ListItem=ListItem, Dialog=Dialog, Window=Window,
           DialogProgressBG=DialogProgressBG,
           WindowXMLDialog=WindowXMLDialog,
           NOTIFICATION_INFO=NOTIFICATION_INFO,
           NOTIFICATION_ERROR=NOTIFICATION_ERROR,
           NOTIFICATION_WARNING=NOTIFICATION_WARNING,
           INPUT_ALPHANUM=INPUT_ALPHANUM)

    class _Stat:
        """Kodi's file stat. Tests override sizes through Stat.sizes."""
        sizes = {}

        def __init__(self, path):
            self.path = path

        def st_size(self):
            return _Stat.sizes.get(self.path, 0)

    def _add_dir(handle, items, total=None):
        recorder.directories.extend(items)

    def _end(handle, cacheToDisc=False, succeeded=True):
        recorder.ended.append(succeeded)

    def _set_content(handle, content):
        recorder.content = content

    module("xbmcplugin", addDirectoryItems=_add_dir, endOfDirectory=_end,
           setContent=_set_content, setPluginCategory=lambda *a: None,
           setResolvedUrl=lambda h, ok, li: recorder.resolved.append(li),
           addSortMethod=lambda *a: None, SORT_METHOD_EPISODE=0)
    module("xbmcvfs", translatePath=lambda p: p, exists=lambda p: False,
           File=object, mkdirs=lambda p: None, delete=lambda p: None,
           rename=lambda a, b: True, copy=lambda a, b: True,
           rmdir=lambda p, force=False: recorder.rmdirs.append(p) or True,
           listdir=lambda p: ([], []), Stat=_Stat)

    if ADDON_ROOT not in sys.path:
        sys.path.insert(0, ADDON_ROOT)
    return store
