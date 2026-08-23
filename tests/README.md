# Tests

Plain Python, no dependencies. Run them from the repo root:

```sh
python tests/run.py          # everything
python tests/run.py backup   # only suites matching "backup"
python tests/run.py -v       # show output from every suite
```

Each suite is a normal script that asserts as it goes, so you can also run one
on its own and read what it prints:

```sh
python tests/test_backup.py
```

## How they work

`kodistub.py` puts fake `xbmc*` modules on `sys.modules` so `lib/` can be
imported outside Kodi. It records what the add-on did — directory items,
notifications, executed builtins — for tests to assert on. Dialog answers come
from queues you fill before acting, so no test ever blocks.

`fixtures/` holds frozen copies of the provider metadata with the artwork URLs
and long descriptions stripped, plus a fixed watched/resume state. Nothing reads
your real Kodi profile, so results do not drift as you watch things.
