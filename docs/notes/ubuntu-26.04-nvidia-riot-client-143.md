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

## 8. Mouse wheel does not scroll the Electron 43 UI (resolved)

- **Symptom:** the wheel does nothing in the Riot Client UI. This blocked the
  first-login Terms of Service dialog, which only enables "Accept" after the
  text is scrolled to the end; keyboard scrolling did not work either.
- **Root causes:**
  1. `-[NSEvent initWithType:...]` initialized `_timestamp` from
     `[NSDate timeIntervalSinceReferenceDate]` (~8.16e8s since 2001-01-01)
     instead of monotonic system uptime (`mach_absolute_time()`), and
     `-[NSEvent_mouse initWithType:...]` overwrote any explicit `timestamp`
     passed from `X11Display.m`. Chromium's `ui::EventTimeStampFromSeconds`
     compares `[event timestamp]` against `base::TimeTicks::Now()` (monotonic
     uptime) and drops or mis-schedules input events whose timestamps are
     25.8 years in the future.
  2. `CGEventCreateScrollWheelEvent` in `CoreGraphics/CGEvent.m` stored
     `kCGScrollWheelEventFixedPtDeltaAxis1..3` as `[NSNumber numberWithDouble:val]`
     instead of 16.16 fixed-point integers (`(int64_t)(val * 0x00010000)`),
     so `CGEventGetDoubleValueField(cgEvent, kCGScrollWheelEventFixedPtDeltaAxis1)`
     divided the integer value by `65536.0` and returned `0.000015` instead of
     `1.0`.
  3. `-[NSEvent_mouse initWithType:...windowNumber:...]` messaged
     `[(id)windowNumber delegate]`, treating an integer window number as an
     Objective-C pointer when `windowNumber` was non-zero.
- **Fix:**
  - Use `darlingSystemUptime()` (`mach_absolute_time()` scaled via
    `mach_timebase_info`) in `AppKit/NSEvent.subproj/NSEvent.m` and preserve
    explicit timestamps in `AppKit/NSEvent.subproj/NSEvent_mouse.m`.
  - Store `kCGScrollWheelEventFixedPtDeltaAxis1..3` as 16.16 fixed-point
    integers in `CoreGraphics/CGEvent.m` and populate `kCGScrollWheelEventIsContinuous`,
    event location, and modifier flags on `[NSEvent_mouse CGEvent]`.
- **Files:** `patches/cocotron.patch` (`AppKit/NSEvent.subproj/NSEvent.m`,
  `AppKit/NSEvent.subproj/NSEvent_mouse.m`, `CoreGraphics/CGEvent.m`),
  `tools/riot-scroll-event-check.m`.
- **Verified:** `build/riot-scroll-event-check` (`SCROLL_EVENT vertical=pass horizontal=pass phases=none cg_notches=pass`).

## 9. UI does not repaint after some state changes (resolved with #8)

- **Root cause:** Same `[NSEvent timestamp]` epoch mismatch (`CFAbsoluteTime`
  since 2001 vs. `mach_absolute_time()` monotonic uptime) that corrupted
  Chromium's input/compositor frame scheduling in Electron 43.
- **Fix:** Resolved by the monotonic `darlingSystemUptime()` fix in
  `AppKit/NSEvent.subproj/NSEvent.m`.

## 10. Changing the language closes the client (observed)

Changing the language in the UI logs `CloseClientUXAndShutdown` and the UX
exits; the client is not relaunched. The launcher always passes
`--locale=${RCLOL_LOCALE:-en_GB}`. Workaround: set `RCLOL_LOCALE` (for example
`es_MX`) before launching. Investigate whether the relaunch path fails under
Darling.

## 11–13. Other observations (resolved #11 & #12)

- **#11 (`orderedWindowNumbers` & `NSSpellChecker` log spam — resolved):**
  Removed `NSUnimplementedFunction()` from `-[X11Display orderedWindowNumbers]`
  and `NSUnimplementedMethod()` from `NSSpellChecker` (`closeSpellDocumentWithTag:`,
  `isAutomaticSpellingCorrectionEnabled`, `isAutomaticTextReplacementEnabled`),
  and added `NSSpellChecker.m` to `tools/build-riot-appkit-runtime.py`.
- **#12 (Metal `newLibraryWithData: TODO: support actual function calls` — resolved):**
  In `src/iridium/air.cpp` and `private-include/iridium/dynamic-llvm.hpp`, mark
  all non-entry defined functions in the module as `alwaysinline` (removing
  `noinline`/`optnone`) and run `always-inline` at the start of `LLVMRunPasses`
  before `function(sroa,mem2reg,...)` so internal helper functions such as
  `_ZL11sampleDepth...` are inlined before AIR-to-SPIR-V translation.
- **#13 (`launchctl load`):** `launchctl load .../com.riot.riotclient.checkinstalls.plist`
  fails; the client reports `launch_agent_failure` but patching continues.

## 14. Shader and pipeline compilation stutter (resolved)

- **Symptom:** Framerate dips from ~130 FPS down to 30–50 FPS when champions,
  abilities, particles, or new map sectors are encountered for the first time.
  Once all spells have been cast, performance stabilizes at ~133 FPS.
- **Root cause:** In `indium/render-pipeline.cpp` and `compute-pipeline.cpp`,
  `vkCreateGraphicsPipelines` and `vkCreateComputePipelines` passed
  `VK_NULL_HANDLE` as the `VkPipelineCache`. Furthermore, `PrivateDevice::newLibrary`
  re-ran `Iridium::translate` (AIR -> LLVM passes -> SPIR-V) from scratch on
  every library creation without caching.
- **Data from live log (`2026-11-13T20-08-19_r3dlog.txt`):**
  - Average FPS: 133.11 fps (Average Frame Time: 7.51 ms).
  - Normal gameplay reaches 120–140 FPS on NVIDIA RTX 3060 Ti once cached.
- **Fix:**
  - Implemented persistent disk-backed `VkPipelineCache` in
    `Indium::PrivateDevice` (`~/Library/Caches/Indium/vkPipelineCache-<vendorID>-<deviceID>.bin`),
    validating `VkPipelineCacheHeaderVersionOne` against `vendorID`, `deviceID`,
    and `pipelineCacheUUID`, and saving atomically via temp file + `rename()`.
  - Added in-memory and disk-backed SPIR-V + reflection caching in
    `Indium::PrivateDevice::newLibrary` (`~/Library/Caches/Indium/iridium-v3-<h164>-<h264>-<len>.bin`)
    so repeated shader libraries skip `Iridium::translate` and reuse existing
    `VkShaderModule` handles when live on the same device.
  - Added `StorageMode::Private` (`DEVICE_LOCAL`) fallback in
    `Indium::ConcreteTexture` when optimal-tiling images on discrete NVIDIA GPUs
    expose only non-host-visible `memoryTypeBits`.
- **Files:** `patches/metal--deps--indium.patch` (`src/indium/device.cpp`,
  `src/indium/dynamic-vk.cpp`, `src/indium/library.cpp`,
  `src/indium/render-pipeline.cpp`, `src/indium/compute-pipeline.cpp`,
  `src/indium/texture.cpp`).
- **Verified:** `build/riot-metal-sampler-check` populates and reloads
  `vkPipelineCache-10de-2489.bin` and `iridium-v3-*.bin` in
  `~/.darling/Users/miguem/Library/Caches/Indium/`.

## 15. In-game graphics quality change freezes the game for ~28 seconds (resolved)

- **Symptom:** Changing graphics quality settings (e.g., textures, shadows,
  effects) causes a complete game freeze lasting ~28 seconds.
- **Log trace:**
  `000689.524| ALWAYS| PRFW| Detected very long frame: 28.170s`
- **Root cause:** Changing quality settings in League calls
  `r3dRenderLayer::RecreateOwnedResources`, wiping all pipeline state objects.
  Every shader was synchronously re-translated through `Iridium::translate` and
  recompiled into 3 Vulkan pipelines with `VK_NULL_HANDLE` cache on the main
  render thread.
- **Fix:** Resolved by the in-memory + disk-backed `Iridium::translate` library
  cache, persistent `VkPipelineCache`, lazy topology pipeline creation, and
  cached compute pipelines in `patches/metal--deps--indium.patch`.

## 16. Match exit / game window hang for 10–11 seconds (resolved)

- **Symptom:** When a match finishes and the player confirms exit, the game
  window freezes in place for 10 to 11 seconds before finally closing and
  returning to the post-game lobby.
- **Log trace (`r3dlog.txt`):**
  - `000975.987| ALWAYS| CONN| Requesting disconnect (19)`
  - `000976.032| ALWAYS| FLOW| Finished Main Loop`
  - `000987.305| ALWAYS| FLOW| LCU Game End Status: GAMEFLOW_EVENT.GAME_COMPLETED Result: 200: SUCCESS` (11.273s delta)
  - `000988.261| ALWAYS| FLOW| Destroying the renderer`
- **Root cause:** The main game loop stops pumping events at
  `Finished Main Loop`, leaving the X11/Wayland window frozen while the
  reliable UDP network layer waits up to its 8-second disconnect timeout
  (`Timeout=8` in `netstats`) for a server disconnect ACK before notifying LCU.
- **Fix:** Added `GameExitWatch` and `hide_finished_game_window` in
  `tools/session-supervisor.py` to tail the active `*_r3dlog.txt` from its
  initial EOF and immediately `XUnmapWindow` the frozen `LeagueofLegends` X11
  window as soon as `Finished Main Loop` is logged, while allowing the process
  to finish its LCU `GAME_COMPLETED` handshake in the background.
- **Files:** `tools/session-supervisor.py`, `tests/test_session_supervisor.py`.

## 17. Arena game mode severe frame pacing hitches (resolved)

- **Symptom:** Playing the Arena game mode (2v2v2v2) results in recurrent and
  pronounced frame hitching and micro-stutters across the entire duration of the
  match, unlike Summoner's Rift or ARAM which stabilize after the first minutes.
- **Data from live log (`2026-11-13T20-31-23_r3dlog.txt`):**
  - Average FPS: 94.89 fps (Average Frame Time: 10.54 ms).
  - Maximum Frame Time: 1029.67 ms (a full 1.03-second freeze at `000677.727`).
  - Frame time histogram showed over 9,500 frames spiking above 18 ms (>55ms
    frametime drops), with 2,065 frames dropping below 30 FPS.
  - Network logs confirmed 0 packet loss and stable 38 ms ping, verifying the
    stutters are purely client-side rendering bottlenecks.
- **Root cause:**
  1. *Continuous dynamic Augment selection, sub-arena rotation, and hazard cameos:*
     Each Arena round dynamically binds new spell, terrain, and particle shaders.
  2. *Triple pipeline compilation overhead:* In `indium/render-pipeline.cpp`,
     `recreatePipeline` eagerly compiled 3 separate Vulkan pipelines (Point
     List, Line List, Triangle List) synchronously with `VK_NULL_HANDLE`, even
     though >99% of League draw calls use Triangle topologies.
  3. *Uncached compute pipelines:* `PrivateComputeCommandEncoder::dispatchThreadgroups`
     created and destroyed a fresh `VkPipeline` on every compute dispatch.
- **Fix:**
  - `PrivateRenderPipelineState` now compiles only the triangle-topology
    `VkPipeline` eagerly (using `_privateDevice->pipelineCache()`) and compiles
    point/line topologies lazily on demand via `pipelineForTopologyIndex()`.
  - `PrivateComputePipelineState` now caches `VkPipeline` handles per
    `threadsPerThreadgroup` via `pipelineForThreadgroup()` using
    `_privateDevice->pipelineCache()`.
  - Combined with disk-backed `VkPipelineCache` and `Iridium::translate` library
    caching, shader and pipeline compilation hitches are eliminated.

---

## Pull request plan

Upstream asks for focused PRs, DCO sign-off on every commit (`git commit -s`)
and `scripts/validate.py`, the unit tests and `scripts/prepare.py --check-only`
passing. Each PR branches from `upstream/main`:

- **A** `fix/ubuntu-nvidia-launcher`: entries 1–4 & 16 (Xwayland cookie, NVIDIA Vulkan ICD pinning, NVIDIA PCIe/VRAM `system_profiler` + `doctor.py`, match-exit window hiding in `session-supervisor.py`).
- **B** `fix/libkqueue-atfork`: entry 5 (`knote_free_all_atfork()`).
- **C** `fix/foundation-constant-number-unsigned`: entry 6 (`-[NSConstantIntegerNumber compare:]`).
- **D** `fix/indium-air-unpack`: entries 7 & 12 (`air.unpack.*` lowering and `always-inline` pass for internal AIR helper functions).
- **E** `fix/x11-scroll-wheel`: entries 8, 9 & 11 (monotonic `NSEvent` timestamps, 16.16 fixed-point `CGEvent` scroll deltas, `orderedWindowNumbers` & `NSSpellChecker` cleanup).
- **F** `opt/indium-pipeline-cache`: entries 14, 15 & 17 (persistent `VkPipelineCache`, disk/memory `Iridium::translate` library cache, lazy point/line topology compilation, cached compute pipelines, and NVIDIA optimal-tiling `StorageMode::Private` fallback).
