# Notch Music prototype

A small background controller for music playing in another app. It does **not** play audio files itself. The first prototype provides a tray icon and a top-center black notch. It rests as a plain, small black bump, expands on hover or from the tray, and contracts after the pointer leaves. The expanded panel shows current track, artist, source, play/pause, previous, and next.

## Run

Python 3.11 or newer is required. From this directory:

```text
python -m pip install --target .vendor -r requirements.txt
python -m music_app --status
python -m music_app
```

`--status` prints one media-session snapshot and exits. `--show` opens the panel immediately; otherwise it starts as the small notch. Hover over it to expand it. Move away or press Escape to contract it. The tray menu also has **Open music notch** and **Quit**. On Windows, `pythonw -m music_app` starts without a console after dependencies are installed.

## Platform support

| Platform | Integration | Current scope |
| --- | --- | --- |
| Windows 10/11 | Global System Media Transport Controls | Metadata and play/pause/skip for players that publish a Windows media session. Verified against a Spotify session on Windows. |
| Linux desktop | MPRIS over D-Bus | Metadata and play/pause/skip for MPRIS players. Needs a desktop session with D-Bus. Not yet verified on Linux hardware. |
| macOS | AppleScript | Music and Spotify only. Other players, including browser tabs, are not yet integrated. Not yet verified on macOS hardware. |

When no supported player is available, the panel disables its controls. The app does not ask for administrator rights, send media data over the network, or start a local playback service.

The curved transparent window is implemented on Windows. macOS and Linux currently use an opaque black window until native transparent-window adapters are built and tested there. The album tile is a visual placeholder; the playback line is an activity accent, not a seek bar or claimed playback position.

## Resource footprint

The prototype checks media state every two seconds with the panel open and every eight seconds while hidden. On the current Windows development machine, the running Python/Tk/Pystray prototype measured about **63 MB working set**. This does **not** meet the final low-memory ambition. The desktop shell should be reworked in native code and measured on all target platforms before release; no footprint claim should be based on this prototype alone.

## Next work

- Verify tray and controls interactively on Windows; test actual play/pause and skipping with a disposable session.
- Test Linux and macOS adapters on those operating systems and handle their permission and desktop-environment differences.
- Replace polling with platform media-session events where possible.
- Move the UI and background core to a native implementation once the feature behavior is settled and measure RAM, CPU wakeups, and binary size.

The broader app plan, including process recommendations and screenshots, is in [DESIGN.md](DESIGN.md).
