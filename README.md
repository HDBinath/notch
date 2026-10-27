# Notch Music prototype

A small background controller for music playing in another app. It does **not** play audio files itself. The prototype provides a tray icon and a top-center black notch with three stages: a minimal bump, a half-open strip with a live clock and playback indicator, and a full player. Hovering or opening from the tray shows the full player. After the pointer leaves, it shrinks to the half-open strip, then returns to the minimal bump after 60 seconds. The full player shows current track, artist, source, play/pause, previous, and next.

## Run

Python 3.11 or newer is required. From this directory:

```text
python -m pip install --target .vendor -r requirements.txt
python -m music_app --status
python -m music_app
```

`--status` prints one media-session snapshot and exits. `--show` opens the full panel immediately; otherwise it starts as the minimal notch. Hover over the minimal or half-open notch to expand it. Moving away or pressing Escape moves full → half-open; pressing Escape again moves half-open → minimal. The tray menu also has **Open music notch** and **Quit**. On Windows, `pythonw -m music_app` starts without a console after dependencies are installed.

## Platform support

| Platform | Integration | Current scope |
| --- | --- | --- |
| Windows 10/11 | Global System Media Transport Controls | Metadata and play/pause/skip for players that publish a Windows media session. Verified against a Spotify session on Windows. |
| Linux desktop | MPRIS over D-Bus | Metadata and play/pause/skip for MPRIS players. Needs a desktop session with D-Bus. Not yet verified on Linux hardware. |
| macOS | AppleScript | Music and Spotify only. Other players, including browser tabs, are not yet integrated. Not yet verified on macOS hardware. |

When no supported player is available, the panel disables its controls. The app does not ask for administrator rights, send media data over the network, or start a local playback service.

The curved transparent window is implemented on Windows. macOS and Linux currently use an opaque black window until native transparent-window adapters are built and tested there. The image tile shows the current item's artwork or video thumbnail when the player supplies one locally; otherwise it shows a placeholder. The playback line shows reported progress when position and duration are available. It is display-only. Remote artwork URLs are never fetched, so some players will show the placeholder even when they publish a cover URL.

## Resource footprint

The prototype checks media state every two seconds in the half-open or full stage and every eight seconds in the minimal stage. Battery state is read locally every 30 seconds while visible; machines without a readable battery show no ring. The ring is green with a white bolt while charging, red with a white bolt below 20% while discharging, and otherwise shows green charge progress. On the current Windows development machine, the earlier Python/Tk/Pystray prototype measured about **63 MB working set**. This does **not** measure the current three-stage version or meet the final low-memory ambition. The desktop shell should be reworked in native code and measured on all target platforms before release; no footprint claim should be based on this prototype alone.

## Next work

- Verify tray and controls interactively on Windows; test actual play/pause and skipping with a disposable session.
- Test Linux and macOS adapters on those operating systems and handle their permission and desktop-environment differences.
- Replace polling with platform media-session events where possible.
- Move the UI and background core to a native implementation once the feature behavior is settled and measure RAM, CPU wakeups, and binary size.

The broader app plan, including process recommendations and screenshots, is in [DESIGN.md](DESIGN.md).
