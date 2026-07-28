# System Architecture & Technical Report
## Raspberry Pi Gesture Engine & Transparent Overlay Kiosk

---

## 1. Executive Summary

This project implements a high-performance, real-time hand gesture tracking and visual feedback overlay system engineered for interactive kiosks (e.g., Raspberry Pi or desktop environments). 

The system operates as a decoupled two-process architecture:
1. **`gesture_engine.py`**: A headless, multi-threaded background engine responsible for webcam frame acquisition, lighting normalization, multi-hand proximity lock-on, 3D/2D landmark processing, stateful gesture interpretation (dwell-to-click, swipes), and WebSocket broadcasting.
2. **`overlay.py`**: A lightweight, transparent PyQt5 window that renders an auto-scaling, high-contrast 21-landmark hand skeleton over the OS interface without intercepting touch or mouse events.

---

## 2. Technology Stack & Dependencies

| Component | Library / Tool | Function & Role |
| :--- | :--- | :--- |
| **Language** | Python 3.8+ | Core runtime environment. |
| **Computer Vision** | OpenCV (`cv2`) | Camera capture (V4L2/DirectShow), MJPEG hardware stream decode, downscaling, and LAB CLAHE glare suppression. |
| **ML Hand Tracking** | MediaPipe Tasks API (`0.10+`) | `HandLandmarker` model (`float16`), extracting 21 normalized and metric 3D world landmarks per hand. |
| **GUI & Overlay** | PyQt5 & `qasync` | Transparent, frameless, stay-on-top window using native XCB/X11 rendering (`X11BypassWindowManagerHint`). `qasync` unifies Qt's event loop with Python `asyncio`. |
| **Inter-Process Comm.** | WebSockets (`websockets`) | Asynchronous, low-latency JSON event broadcaster running on `ws://localhost:8765`. |
| **OS Input Control** | `pynput` / `evdev` / `xdotool` | System mouse positioning and left-click injection when dwell-to-click triggers. |

---

## 3. System Architecture & Data Flow

```
                               ┌────────────────────────────────────────────────────────┐
                               │                    CAMERA SENSOR                       │
                               └───────────────────────────┬────────────────────────────┘
                                                           │ (Webcam 640x480 @ 30-60 FPS)
                                                           ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ GESTURE ENGINE (gesture_engine.py)                                                                          │
 │                                                                                                             │
 │  ┌─────────────────────────────────┐   ┌─────────────────────────────────┐   ┌───────────────────────────┐  │
 │  │ 1. Frame Crop & Lighting Norm.  │──>│ 2. MediaPipe HandLandmarker     │──>│ 3. HandLockTracker        │  │
 │  │    (INTER_AREA 320x240, CLAHE)  │   │    (num_hands=2, LIVE_STREAM)   │   │    (Closest Hand Lock)    │  │
 │  └─────────────────────────────────┘   └─────────────────────────────────┘   └─────────────┬─────────────┘  │
 │                                                                                            │                │
 │  ┌─────────────────────────────────┐   ┌─────────────────────────────────┐                 │                │
 │  │ 6. WebSocket JSON Server        │<──│ 5. GestureFSM & Mouse Injector │<────────────────┘                │
 │  │    (ws://localhost:8765)        │   │    (Dwell Timer & Vector Lock)   │    4. Adaptive Landmark EMA  │
 │  └────────────────┬────────────────┘   └─────────────────────────────────┘       & OneEuro Cursor Filter    │
 └───────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┘
                     │
                     │ (JSON Stream @ ~60 FPS: Cursor X/Y, 21 Landmarks, depth_z, dwell_progress, scale_x/y)
                     ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ TRANSPARENT OVERLAY (overlay.py)                                                                            │
 │                                                                                                             │
 │  ┌─────────────────────────────────┐   ┌─────────────────────────────────┐   ┌───────────────────────────┐  │
 │  │ 1. WebSocket Consumer           │──>│ 2. Isotropic Auto-Scaler        │──>│ 3. QPainter Skeleton     │  │
 │  │    (Direct Shared State Read)   │   │    (depth_z^0.65 1:1 Scaling)   │   │    (Streamlined Glove UI) │  │
 │  └─────────────────────────────────┘   └─────────────────────────────────┘   └───────────────────────────┘  │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Algorithmic Logic & State Machines

### 4.1. Multi-Hand Proximity & Lock-On (`HandLockTracker`)
- **Closest Hand Selection**: Evaluates 2D normalized palm span metric $S_{palm} = \|LM_9 - LM_0\|_{2D}$ (Wrist to Middle MCP distance) for all detected hands. Larger $S_{palm}$ indicates a hand closer to the camera lens.
- **20% Hysteresis Margin**: Prevents hand-swapping jitter when two hands are in frame. A competing non-locked hand must be at least 20% larger ($S_{new} > 1.20 \times S_{locked}$) to hijack control.
- **Spatial Tracking Continuity & Miss Grace**: Tracks palm center position $C = \frac{LM_0 + LM_9}{2}$ within a normalized distance radius ($d < 0.35$). Holds lock during temporary frame drops for up to $0.25\text{s}$.

### 4.2. Perpendicular Pointing Vector Lock ($R_f$)
- **Foreshortening Metric**: $R_f = \frac{\|LM_8 - LM_5\|_{2D}}{\|LM_5 - LM_0\|_{2D} + 1e-6}$.
- **Palm Direction Lock**: When pointing straight at the camera lens ($R_f < 0.35$ and index finger extended), 2D vector direction between $LM_5$ and $LM_8$ collapses. The engine locks index finger orientation along the stable palm vector axis $u_{05} = \frac{LM_5 - LM_0}{\|LM_5 - LM_0\|}$:
  $$LM_8 = LM_5 + \|LM_8 - LM_5\|_{2D} \cdot u_{05}$$
  $$LM_6 = LM_5 + 0.33 \cdot (LM_8 - LM_5), \quad LM_7 = LM_5 + 0.66 \cdot (LM_8 - LM_5)$$

### 4.3. Multi-Stage Lighting Normalization (`normalize_frame_lighting`)
- Splits the frame into Left/Right regional halves to process overexposed light regions independently.
- Applies LAB color space CLAHE (`clipLimit=2.0`, `tileGridSize=(4,4)`) and gamma adjustment ($\gamma = 1.4$) to extract landmark features under harsh lighting or overhead glare.

### 4.4. Smooth Motion & Tremor Filtering
- **Adaptive Landmark EMA**: Dynamically scales landmark EMA smoothing factor $\alpha$ based on palm speed $v_{palm}$ and foreshortening ratio $R_f$:
  $$\alpha_{val} = \text{clip}\left(0.12 + (\alpha_{\max} - 0.12) \cdot (1 - e^{-3.0 \cdot v_{palm}}), 0.12, \alpha_{\max}\right)$$
  where $\alpha_{\max} = 0.40$ during foreshortening and $0.55$ during general motion.
- **OneEuro Cursor Filter**: Applies non-linear filtering (`min_cutoff=0.8, beta=0.7, d_cutoff=1.0`) to index fingertip screen coordinates for fluid cursor motion across the display without velocity jitter.
- **Micro-Tremor Deadzone**: Freezes pixel output when hover movement is $< 2.0\text{px}$.

### 4.5. Dwell-to-Click Finite State Machine (`GestureFSM`)
- **States**: `MOVE` $\to$ `DWELL` $\to$ `CLICK`.
- **Trigger**: Fired when index fingertip stays within `DWELL_RADIUS_PX` ($48\text{px}$) for `DWELL_DURATION_S` ($1.2\text{s}$).
- **Cooldown & Protection**: Enforces an $0.8\text{s}$ cooldown post-click and a 20-frame ($~320\text{ms}$) ghost-protection window to prevent input disruption during OS click injection.

### 4.6. Point-Mode Skeleton Decoupling & Unified Dwell UI (`overlay.py`)
- **Point-Mode Streamlining**: When $R_f < 0.35$, intermediate index bone connections $(5\to6, 6\to7, 7\to8)$ collapse into a single clean line ($LM_5 \to LM_8$) and interior joint dots ($LM_6, LM_7$) are suppressed to eliminate visual crumbling.
- **Isotropic Auto-Scaling**: Computes scale factor $S_{auto} = \text{clamp}(depth\_z^{0.65}, 0.70, 1.40)$ using 1:1 screen metric `base_size = min(width, height)`.
- **Unified Dwell Circle UI**: Renders a dark forest green base track with a black contrast border and a Vivid Neon Lime Green (`#39FF14`) progress arc.

---

## 5. System Parameters & Configuration Reference

| Parameter Name | Value | Module | Description |
| :--- | :--- | :--- | :--- |
| `CAMERA_INDEX` | `0` | `gesture_engine.py` | Index of webcam device. |
| `CAMERA_WIDTH` / `HEIGHT` | `640` x `480` | `gesture_engine.py` | Camera capture resolution. |
| `TARGET_FPS` | `60` | `gesture_engine.py` | Engine loop target frame rate. |
| `CAMERA_PHYSICALLY_ROTATED` | `False` | `gesture_engine.py` | Camera mounting orientation flag. |
| `num_hands` | `2` | `gesture_engine.py` | Max hands tracked by MediaPipe. |
| `DETECT_CONFIDENCE` | `0.60` | `gesture_engine.py` | Min confidence for full-frame hand detection. |
| `TRACK_CONFIDENCE` | `0.40` | `gesture_engine.py` | Min confidence for incremental hand tracking. |
| `PRESENCE_CONFIDENCE` | `0.40` | `gesture_engine.py` | Min confidence for hand presence. |
| `DWELL_DURATION_S` | `1.2` s | `gesture_engine.py` | Stillness duration required for click. |
| `DWELL_RADIUS_PX` | `48` px | `gesture_engine.py` | Max allowed drift for dwell timer. |
| `SWIPE_VELOCITY_THRESHOLD` | `1.0` units/s | `gesture_engine.py` | Min velocity to trigger swipe gesture. |
| `REFERENCE_SIZE` | `0.18` | `gesture_engine.py` | Baseline palm metric at nominal ~50cm distance. |
| `WS_HOST` / `PORT` | `localhost:8765` | `gesture_engine.py` | WebSocket server configuration. |
| `REFRESH_MS` | `16` ms | `overlay.py` | Repaint timer interval (~60 FPS). |
| `FADE_SPEED` | `0.12` | `overlay.py` | Skeleton fade-in / fade-out rate. |
| `_DWELL_GREEN_LOAD` | `#39FF14` | `overlay.py` | Vivid Neon Lime Green dwell arc color. |

---

## 6. Verification & Health Audit

- **Syntax & Compilation**: Verified using `python -m py_compile gesture_engine.py overlay.py` (0 errors).
- **Headless & Display Compatibility**: Fully compatible with Linux/Raspberry Pi (X11/Wayfire via `xcb` and `xrandr`) and Windows dev environments.
