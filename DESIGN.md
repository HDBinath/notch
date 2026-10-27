# Personal desktop notch: implementation brief

## Goal

Build a small, resident desktop utility for Windows, macOS, and Linux. The first usable version focuses on current music, system controls, resource diagnosis, and quick screenshots. It has a tray/menu-bar icon and an optional top-edge overlay. The overlay is a quick control surface, not a web page or a scheduled background job. Low RAM, CPU, and disk impact of the utility itself are release requirements.

## Reference and evidence

Nusa's website advertises music controls, synchronized lyrics, focus timer, water reminders, file shelf, to-do reminders, volume and brightness overlays, battery/network/headphone indicators, and a resting mascot. It says the Windows app is native Rust, stores personal data locally, and uses LRCLIB for lyrics. Its public GitHub repository currently contains the website and a binary release, but no desktop source. Treat resource and privacy statements as claims, not independently verified measurements.

Reference: https://rama2555.github.io/nusa/ and https://github.com/rama2555/nusa

## First usable version

1. The app starts in the system tray or menu bar; the user can quit from there. Its resting UI is a very small, plain black bump at the top center of the screen, with no eyes, labels, or other ornament. Hovering expands it into the full player. Moving away shrinks it to a half-open strip with a clock, playback indicator, and battery ring when available; after 60 seconds it returns to the minimal bump.
2. The expanded overlay shows track title, artist, artwork when available, playback state, and play/pause, previous, next controls.
3. A volume control shows the current output volume and can change it. Mute has a visible state.
4. The overlay can be opened by clicking the tray icon or a configurable shortcut. Hover behavior is optional because it can conflict with top-edge controls.
5. The overlay avoids full-screen apps and can be repositioned to the top of any chosen monitor.
6. Settings persist locally: launch at sign-in, monitor, position, shortcut, autohide, reduced motion, and enabled integrations.
7. If an OS or player does not expose media metadata/control, the UI says so and leaves unavailable controls disabled.
8. A compact system monitor shows RAM used/total and disk activity. Its expanded view shows per-drive free space separately from current read/write activity; these are different measures and should not share one unlabeled number.
9. A screenshot button opens a small menu for full screen, selected window, or selected region. The result can be copied to the clipboard or saved locally. Never upload screenshots automatically.
10. A process inspector ranks user applications by current RAM, sustained RAM growth, disk read/write rate, and recent CPU use. It distinguishes high use from actual pressure and shows why an app was flagged.
11. For a selected user application, offer a normal close request first. Recheck available RAM and performance afterwards. A forced end is a separate, explicit action with a warning about unsaved work. Never automatically end OS services, security software, drivers, or the utility itself.

The user chose recommendation-only behavior: the app never closes a process on its own. It explains the suspected issue and waits for the user to select an action.

Brightness should be a separately enabled capability because external monitors and Linux desktop environments vary. Lyrics, tasks, water, and file shelf are later modules.

## Improvements over the reference

- **Platform choice:** one interface with explicit Windows, macOS, and Linux capability adapters.
- **Control and accessibility:** keyboard navigation, screen-reader labels, reduced-motion mode, high-contrast theme, configurable placement, and a global hide shortcut.
- **Predictable behavior:** clear rules for multiple players, monitor changes, full-screen apps, and disconnected devices.
- **Privacy and trust:** local settings, no account, no analytics, explicit disclosure for any network feature, published source and reproducible build instructions.
- **User control:** every module and notification can be enabled independently; quiet hours and rate limits prevent repeated interruptions.
- **Performance:** event-driven updates where supported; benchmark idle CPU, memory, wakeups, startup time, and battery impact on each OS before making claims.
- **Evidence before action:** flag sustained pressure or unresponsive behavior, not a process merely because it uses a large amount of RAM. Show current and recent values, the process identity, and whether it belongs to the signed-in user.
- **Safe cleanup:** let the user choose a process, request graceful close, and show measured before/after available memory. Do not offer a misleading generic "RAM boost" or clear OS file caches to make a number look better.
- **Useful monitoring:** keep the closed bump visually empty and show measurements only in the expanded panel; sample RAM and disk at a modest interval and stop sampling when the monitor is disabled.
- **Quick capture:** one click or shortcut reaches region capture, with a visible destination and no network transfer.

## Architecture

Use a small native core with a shared UI and typed platform adapters. Tauri 2 is one possible shell because it supports tray icons and configurable transparent/always-on-top windows, but its webview memory cost must be measured against a native-window alternative before committing to it. Keep OS calls behind adapters so missing capabilities do not break the overlay.

`MediaAdapter` exposes available sessions, active session selection, metadata, play/pause, previous, and next, with change events. Windows can use Global System Media Transport Controls; Linux can use MPRIS over D-Bus. macOS needs a separate feasibility spike for controlling *other* apps: Apple's documented Now Playing APIs primarily describe publishing and controlling an app's own playback, so do not promise universal macOS player support without testing specific players and supported APIs.

`SystemAdapter` exposes volume and mute first. Brightness is an optional second step. The UI consumes adapter capabilities and never displays a working-looking control if its platform implementation is absent.

`MetricsAdapter` exposes RAM used/total, disk read/write rate, and volume free/total with timestamps. Label these distinctly. Refresh visible figures around once per second, and use a slower interval or suspend sampling when hidden. Do not claim zero CPU use while the monitor is active.

`ProcessAdapter` enumerates processes at a slow interval while the inspector is open or system pressure is sustained. Collect only the needed counters, calculate disk throughput from counter deltas, and keep a short in-memory history. The analyzer considers available memory, swap/page pressure, sustained per-process private/resident memory, disk throughput, and whether the process is unresponsive. The action layer revalidates PID and process start time to avoid acting on a reused PID, checks ownership and protection rules, requests graceful close, and verifies the result. Forced termination is a distinct user action. No elevated privileges by default.

`CaptureAdapter` supports screen, window, and region capture as the platform permits. Keep capture entirely local, let the user choose copy or save, and exclude the overlay itself where the OS capture API allows. Screenshot permissions and selection UI will vary by platform.

## Milestones and acceptance checks

1. **Shell:** launch, tray menu, overlay, position persistence, quit. Verify on Windows first, then macOS and Linux.
2. **Media:** detect and control at least two common media players on each supported OS; define a clear fallback where OS APIs differ.
3. **Audio:** current volume, set volume, mute, device changes.
4. **Polish:** fullscreen suppression, multiple monitors, keyboard/screen-reader use, reduced motion, autostart.
5. **Metrics and diagnosis:** RAM, disk activity, drive capacity, top resource users, sustained-pressure explanations, and process identity safeguards.
6. **Cleanup:** user-selected graceful close and measured before/after results; separate forced end flow for user applications.
7. **Capture:** screen/window/region screenshots, copy/save flow, and shortcuts.
8. **Optional modules:** brightness, lyrics with opt-in network access, focus and reminders, file shelf.

For a release, test sleep/wake, monitor hot-plugging, player switching, no active player, OS permission denial, multi-monitor screenshots, clipboard and save paths, process exit/restart and PID reuse, protected processes, unsaved documents, and quitting/relaunching. Measure RAM, CPU, disk I/O, wakeups, and binary size on each OS with the overlay closed, monitor active, and screenshot flow open. Set numerical budgets only after a baseline prototype on all three platforms.

## Current development environment

This workspace is empty. Node.js is installed, but Rust/Cargo and a .NET SDK are not, and Electron is not installed. A Tauri build cannot be compiled or tested here until the desktop toolchain and dependencies are available. The implementation can start with the shell and platform adapters once that toolchain is in place.
