# Ubuntu 26.04 + NVIDIA + Riot Client 143: issue log

Working log of the compatibility problems found while bringing up the client on
Ubuntu 26.04 (GNOME/Mutter Wayland, Xwayland) with an NVIDIA RTX 3060 Ti
(driver 595.99.02) and Riot Client 143.0.5.5260 (Electron 43.8.4). Each entry
records the symptom, root cause, fix, files and how it was verified, so every
fix can be submitted upstream as a focused pull request.

All fixes are in the Darling compatibility layer or the launcher. No Riot
binary is modified and no debugger is attached to client or game processes.

Status: **fixed** (in the fork, verified live), **pending** (understood, not
fixed yet), **observed** (seen, not investigated yet).

Workflow (ongoing, not a one-off): every new problem found while playing
(Normals/ARAM) gets an entry here first; it is fixed in the compatibility layer
with a regression test, verified live, and then shipped upstream as its own
focused PR. Fixed entries stay listed with the PR link once opened.

| # | Area | Problem | Status | Planned PR |
|---|------|---------|--------|------------|
| 1 | launcher | Xwayland auth cookie not found on GNOME/Mutter | fixed | A |
| 2 | launcher | Vulkan loader may pick llvmpipe over NVIDIA | fixed | A |
| 3 | profiler/doctor | NVIDIA model and VRAM missing | fixed | A |
| 4 | installers | Overwriting read-only SDK binaries prompts | fixed | A |
| 5 | libkqueue | `fork()` child corrupts the parent's epoll set | fixed | B |
| 6 | Foundation | Unsigned constant `NSNumber` compares wrongly | fixed | C |
| 7 | Indium (Metal) | `air.unpack.*` intrinsics not lowered | fixed | D |
| 8 | AppKit X11 | Mouse wheel does not scroll the Electron 43 UI | pending | E |
| 9 | AppKit X11 | UI does not repaint after some state changes | observed | E? |
| 10 | Riot Client | Changing the language closes the client | observed | - |
| 11 | AppKit X11 | `-[X11Display orderedWindowNumbers]` unimplemented | observed | - |
| 12 | Indium (Metal) | Non-inlined helper calls in blit shaders | observed | - |
| 13 | launchd | `com.riot.riotclient.checkinstalls` LaunchAgent fails | observed | - |

---

## 1. Xwayland auth cookie not found on GNOME/Mutter

- **Symptom:** `iokitd: cannot open X display :0 ... set XAUTHORITY to a readable cookie`;
  no window appears.
- **Root cause:** Mutter writes the Xwayland cookie to
  `$XDG_RUNTIME_DIR/.mutter-Xwaylandauth.*`; `~/.Xauthority` does not exist.
  The launcher looked for the cookie only after a helper could already start
  Darling, so `launchd`/`iokitd` inherited no `XAUTHORITY`.
- **Fix:** discover the cookie before any helper runs, export `XAUTHORITY`, and
  copy it to `~/.Xauthority` (mode 0600) only when that file is missing.
- **Files:** `tools/run-riot-client.sh`, `scripts/preflight.py`.
- **Verified:** client window maps on GNOME Wayland without manual `XAUTHORITY`.

## 2. Vulkan loader may pick llvmpipe over NVIDIA

- **Symptom:** software rendering when Mesa's `lvp_icd.json` is installed next
  to `nvidia_icd.json`.
- **Fix:** when NVIDIA (PCI vendor `0x10de`) is the only DRM GPU, pin
  `VK_DRIVER_FILES`/`VK_ICD_FILENAMES` to the NVIDIA ICD; export host VRAM from
  `nvidia-smi` as `RCLOL_HOST_GPU_VRAM_MB`.
- **Files:** `tools/run-riot-client.sh`, `scripts/preflight.py` (headless
  `vulkaninfo` fallback).

## 3. NVIDIA model and VRAM missing in system_profiler/doctor

- **Fix:** read `/proc/driver/nvidia/gpus/<PCI_SLOT_NAME>/information` so
  `SPDisplaysDataType` and `doctor.py` report model, `PCIe` bus and VRAM.
- **Files:** `tools/darling-system_profiler.sh`, `tools/doctor.py`, tests in
  `tests/test_system_profiler.py`.

## 4. Overwriting read-only SDK binaries prompts

- **Symptom:** `mv` asks for confirmation over `0555` binaries in
  non-interactive installs.
- **Fix:** `mv -f`.
- **Files:** `tools/install-riot-presentation-runtime.sh`,
  `tools/install-riot-gpu-runtime.sh`, `tools/install-riot-metal-pair.sh`.

## 5. libkqueue: fork() child corrupts the parent's epoll set

- **Symptom (Riot Client 143):** splash stays invisible/blocks clicks, the main
  window never appears, the session supervisor stops after 60 s.
- **Root cause:** after `posix_spawn()`/`fork()`, `kqueue_atfork()` freed every
  kqueue in the child through `filter_unregister_all()` → `knote_delete()` →
  `epoll_ctl(EPOLL_CTL_DEL)`. The child's epoll fd refers to the *same* kernel
  `eventpoll` as the parent's, so this removed the parent's registrations
  (Mach port, read, user events) and the parent stalled. When a parent thread
  was inside `kevent64` at fork time (`kq_ref >= 2`), `kqueue_delref()` also
  left the kqueue alive in the child.
- **Fix:** a fork-only teardown (`knote_free_all_atfork()`) that closes the
  child's duplicated helper fds and frees child memory without `epoll_ctl` and
  without taking `kf_knote_mtx`; `_kqueue_close_atfork_cb()` drops every
  `kqmap` slot regardless of `kq_ref`. The epoll fd is now `FD_CLOEXEC`.
- **Files:** `patches/libkqueue.patch` (`src/common/knote.c`,
  `src/common/kqueue.c`, `src/common/private.h`, `src/linux/platform.c`).
- **Verified:** Riot Client starts its GPU, network, renderer and audio
  helpers, the splash closes itself and the main window maps.

## 6. Foundation: unsigned constant NSNumber compares wrongly

- **Root cause:** `-[NSConstantIntegerNumber compare:]` boxed unsigned values
  through `kCFNumberLongLongType`, so large unsigned values compared as
  negative.
- **Fix:** compare unsigned constants directly against signed, unsigned and
  floating-point numbers; build and install the Foundation overlay from
  `scripts/install-runtime.sh`.
- **Files:** `patches/foundation.patch` (`src/NSConstantNumber.m`),
  `scripts/install-runtime.sh`.
- **Verified:** `tools/check-riot-constant-literals.py` (32/32),
  `tests/test_riot_electron43_frameworks.py`.

## 7. Indium: air.unpack.* intrinsics not lowered

- **Symptom:** shader compile failure on `air.unpack.unorm4x8.srgb.v4f32`.
- **Fix:** lower `air.unpack.<unorm4x8|snorm4x8|unorm2x16|snorm2x16>[.srgb]`
  to GLSL.std.450 `Unpack*`, applying the sRGB-to-linear curve to RGB.
- **Files:** `patches/metal--deps--indium.patch` (`src/iridium/air.cpp`).

## 8. Mouse wheel does not scroll the Electron 43 UI (pending)

- **Symptom:** the wheel does nothing in the Riot Client UI. This blocks the
  first-login Terms of Service dialog, which only enables "Accept" after the
  text is scrolled to the end; keyboard scrolling did not work either.
- **Known so far:** X11 buttons 4–7 become `NSScrollWheel` events in
  `X11Display.m` and are routed by `-[NSWindow sendEvent:]` to the hit-tested
  view. Electron's `WebMouseWheelEventBuilder` reads `scrollingDeltaX/Y`,
  `hasPreciseScrollingDeltas`, `phase`/`momentumPhase` and fields of
  `[event CGEvent]` through `CGEventGetIntegerValueField`.
- **Next:** trace which values Chromium reads and why the event is dropped,
  fix in cocotron (AppKit/CoreGraphics), add a regression test.

## 9. UI does not repaint after some state changes (observed)

After a state change made outside the visible page (agreement already accepted)
the window kept showing the old dialog. Possibly related to #8 (input/paint
pumping). Needs investigation.

## 10. Changing the language closes the client (observed)

Changing the language in the UI logs `CloseClientUXAndShutdown` and the UX
exits; the client is not relaunched. The launcher always passes
`--locale=${RCLOL_LOCALE:-en_GB}`. Workaround: set `RCLOL_LOCALE` (for example
`es_MX`) before launching. Investigate whether the relaunch path fails under
Darling.

## 11–13. Other observations

- `-[X11Display orderedWindowNumbers]` is unimplemented (logged when the UX
  closes its windows).
- Metal: `newLibraryWithData: TODO: support actual function calls` for an
  internal `sampleDepth` helper in a blit shader; module-level inlining is not
  run before translation.
- `launchctl load .../com.riot.riotclient.checkinstalls.plist` fails; the
  client reports `launch_agent_failure` but patching continues.

---

## Pull request plan

Upstream asks for focused PRs, DCO sign-off on every commit (`git commit -s`)
and `scripts/validate.py`, the unit tests and `scripts/prepare.py --check-only`
passing. Each PR branches from `upstream/main`:

- **A** `fix/ubuntu-nvidia-launcher`: entries 1–4.
- **B** `fix/libkqueue-atfork`: entry 5.
- **C** `fix/foundation-constant-number-unsigned`: entry 6.
- **D** `fix/indium-air-unpack`: entry 7.
- **E** `fix/x11-scroll-wheel`: entry 8 (and 9 if related), once fixed.
