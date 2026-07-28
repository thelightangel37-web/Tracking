"""
gesture_engine.py
Background gesture-detection engine for a Raspberry Pi kiosk.
Captures frames from webcam, tracks hand landmarks using MediaPipe Tasks API,
filters movement jitter via OneEuro filtering, and broadcasts JSON over WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import platform
import subprocess
import threading
import time
import sys
import urllib.request
import queue
from collections import deque
from typing import List, Optional, Any

import cv2
cv2.setNumThreads(2)
import numpy as np

try:
    import mediapipe as mp
    _api_label = "Tasks API (0.10+)"
except ImportError as exc:
    raise SystemExit(f"mediapipe is not installed: {exc}") from exc

import websockets

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gesture_engine")
log.info("MediaPipe: %s", _api_label)

# Optional pynput mouse injection
try:
    from pynput.mouse import Button as _Button, Controller as _MouseController
    _PYNPUT_AVAILABLE = True
    log.info("pynput available — OS mouse control enabled.")
except ImportError:
    _PYNPUT_AVAILABLE = False
    _Button = None
    _MouseController = None
    log.warning("pynput not installed — OS cursor/click injection disabled.")

# Display & Hardware Configuration
DISPLAY_ORIENTATION: str = "portrait"
_BASE_SHORT: int = 1080
_BASE_LONG: int = 1920

CAMERA_INDEX: int = 0
CAMERA_WIDTH: int = 640
CAMERA_HEIGHT: int = 480
TARGET_FPS: int = 60
CAMERA_PHYSICALLY_ROTATED: bool = False

CAMERA_MARGINS = {
    "landscape": {"x": 0.80, "y": 0.60},
    "portrait": {"x": 0.50, "y": 0.80}
}

def _detect_linux_geometry() -> tuple:
    """Parse xrandr to detect connected display resolution and rotation angle."""
    if platform.system() == "Windows":
        return 0, 0, 0
    try:
        env = dict(os.environ)
        env.setdefault("DISPLAY", ":0")
        r = subprocess.run(
            ["xrandr", "--verbose"],
            capture_output=True, text=True, timeout=3, env=env,
        )
        _ROTATION_MAP = {"normal": 0, "left": 90, "inverted": 180, "right": -90}
        for line in r.stdout.splitlines():
            if " connected" not in line:
                continue
            w, h = 0, 0
            for token in line.split():
                if "x" in token and "+" in token:
                    res = token.split("+")[0]
                    parts = res.split("x")
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        w, h = int(parts[0]), int(parts[1])
                        break
            rotation = 0
            for kw, angle in _ROTATION_MAP.items():
                if f" {kw} " in f" {line} ":
                    rotation = angle
                    break
            if w > 0 and h > 0:
                return w, h, rotation
    except Exception:
        pass
    return 0, 0, 0

class OneEuroFilter:
    """Adaptive low-pass filter for smooth motion without phase lag."""
    def __init__(self, min_cutoff=0.8, beta=0.7, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_prev = None
        self.dx_prev = None
        self.t_prev = None

    def __call__(self, t: float, x: np.ndarray) -> np.ndarray:
        if self.t_prev is None:
            self.x_prev = x.copy()
            self.dx_prev = np.zeros_like(x)
            self.t_prev = t
            return self.x_prev
        te = t - self.t_prev
        if te <= 0.0:
            return self.x_prev
        ad = self._alpha(te, self.d_cutoff)
        dx = (x - self.x_prev) / te
        dx_hat = ad * dx + (1.0 - ad) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * np.linalg.norm(dx_hat)
        a = self._alpha(te, cutoff)
        x_hat = a * x + (1.0 - a) * self.x_prev
        self.x_prev = x_hat
        self.dx_prev = dx_hat
        self.t_prev = t
        return x_hat

    def _alpha(self, te, cutoff):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return te / (te + tau)

class DisplayManager:
    """Manages active display geometry, aspect ratio, and active camera crop margins."""
    def __init__(self):
        self._lock = threading.Lock()
        self.rotation: int = 0
        if DISPLAY_ORIENTATION == "portrait":
            self.target_width, self.target_height = _BASE_SHORT, _BASE_LONG
        else:
            self.target_width, self.target_height = _BASE_LONG, _BASE_SHORT

        if platform.system() != "Windows":
            xr_w, xr_h, xr_rot = _detect_linux_geometry()
            if xr_w > 0 and xr_h > 0:
                self.target_width = xr_w
                self.target_height = xr_h
                self.rotation = xr_rot

        self.active_x_min: float = 0.0
        self.active_x_max: float = 1.0
        self.active_y_min: float = 0.0
        self.active_y_max: float = 1.0
        self.scale_x: float = 0.0
        self.scale_y: float = 0.0
        self._recompute_margins()

    def _recompute_margins(self) -> None:
        if self.target_width == 0 or self.target_height == 0:
            return
        orientation = "landscape" if self.target_width > self.target_height else "portrait"
        margins = CAMERA_MARGINS.get(orientation, CAMERA_MARGINS["landscape"])
        _ax_range = margins["x"]
        _ay_range = (_ax_range * CAMERA_WIDTH * self.target_height) / (CAMERA_HEIGHT * self.target_width)
        if _ay_range > 0.95:
            _ay_range = 0.95
            _ax_range = (_ay_range * CAMERA_HEIGHT * self.target_width) / (CAMERA_WIDTH * self.target_height)

        with self._lock:
            self.active_x_min = round((1.0 - _ax_range) / 2, 4)
            self.active_x_max = round(1.0 - self.active_x_min, 4)
            self.active_y_min = round((1.0 - _ay_range) / 2, 4)
            self.active_y_max = round(1.0 - self.active_y_min, 4)
            self.scale_x = round(self.target_width / _ax_range, 2) if _ax_range else 0.0
            self.scale_y = round(self.target_height / _ay_range, 2) if _ay_range else 0.0

    def get_snapshot(self) -> dict:
        with self._lock:
            return {
                'active_x_min': self.active_x_min,
                'active_x_max': self.active_x_max,
                'active_y_min': self.active_y_min,
                'active_y_max': self.active_y_max,
                'target_width': self.target_width,
                'target_height': self.target_height,
                'scale_x': self.scale_x,
                'scale_y': self.scale_y
            }

    def set_external_geometry(self, w: int, h: int) -> bool:
        if w <= 0 or h <= 0:
            return False
        new_rotation = self.rotation
        if platform.system() != "Windows":
            _, _, xr_rot = _detect_linux_geometry()
            new_rotation = xr_rot
        changed = False
        with self._lock:
            if w != self.target_width or h != self.target_height or new_rotation != self.rotation:
                changed = True
                self.target_width = w
                self.target_height = h
                self.rotation = new_rotation
        if changed:
            self._recompute_margins()
        return changed

    def update(self) -> bool:
        if platform.system() != "Windows":
            return False
        try:
            import ctypes
            user32 = ctypes.windll.user32
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
        except Exception:
            return False
        if w == 0 or h == 0:
            return False
        with self._lock:
            if w == self.target_width and h == self.target_height:
                return False
            self.target_width = w
            self.target_height = h
        self._recompute_margins()
        return True

display_manager = DisplayManager()

# Tracking parameters
DWELL_DURATION_S: float = 1.2
DWELL_RADIUS_PX: int = 48
SWIPE_HISTORY_DURATION_S: float = 0.15
SWIPE_VELOCITY_THRESHOLD: float = 1.0
SWIPE_COOLDOWN_S: float = 0.35
MISS_TOLERANCE_S: float = 0.35

# High-precision confidence thresholds
DETECT_CONFIDENCE: float = 0.70
TRACK_CONFIDENCE: float = 0.55
PRESENCE_CONFIDENCE: float = 0.55

WS_HOST: str = "localhost"
WS_PORT: int = 8765
REFERENCE_SIZE: float = 0.18
ENABLE_SYSTEM_MOUSE: bool = True

_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_landmarker.task")

def _ensure_model() -> None:
    if os.path.exists(_MODEL_PATH):
        return
    log.info("Downloading hand_landmarker.task model...")
    try:
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
    except Exception as exc:
        raise SystemExit(f"Model download failed: {exc}") from exc

class GestureState:
    """Thread-safe container for current hand gesture state and landmarks."""
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._hand_detected = False
        self._gesture = "NONE"
        self._cursor_state = "MOVE"
        self._landmarks = []
        self._dwell_progress = 0.0
        self._cursor_x = 0
        self._cursor_y = 0
        self._skel_anchor_x = 0.0
        self._skel_anchor_y = 0.0
        self._hand_size = 1.0
        self._depth_z = 1.0
        self._hand_pose_mode = "PALM_FACING_CAMERA"
        self._palm_normal = (0.0, 0.0, 1.0)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'hand_detected': self._hand_detected,
                'gesture': self._gesture,
                'state': self._cursor_state,
                'landmarks': list(self._landmarks),
                'dwell_progress': self._dwell_progress,
                'x': self._cursor_x,
                'y': self._cursor_y,
                'skel_anchor_x': self._skel_anchor_x,
                'skel_anchor_y': self._skel_anchor_y,
                'hand_size': self._hand_size,
                'depth_z': self._depth_z,
                'hand_pose_mode': self._hand_pose_mode,
                'palm_normal': self._palm_normal,
            }

    def update(self, x: int, y: int, state: str, gesture: str, hand_detected: bool = False,
               dwell_progress: float = 0.0, landmarks: Optional[List[List[float]]] = None,
               hand_size: float = 1.0, depth_z: float = 1.0) -> None:
        with self._lock:
            self._cursor_x = x
            self._cursor_y = y
            self._cursor_state = state
            self._gesture = gesture
            self._hand_detected = hand_detected
            self._dwell_progress = dwell_progress
            self._landmarks = landmarks if landmarks is not None else []
            self._hand_size = hand_size
            self._depth_z = depth_z

    def to_json(self) -> str:
        with self._lock:
            d = self.snapshot()
            d['dwell_progress'] = round(d['dwell_progress'], 3)
            dm_snap = display_manager.get_snapshot()
            d['active_x_min'] = dm_snap['active_x_min']
            d['active_x_max'] = dm_snap['active_x_max']
            d['active_y_min'] = dm_snap['active_y_min']
            d['active_y_max'] = dm_snap['active_y_max']
            d['screen_width'] = dm_snap['target_width']
            d['screen_height'] = dm_snap['target_height']
            d['scale_x'] = dm_snap['scale_x']
            d['scale_y'] = dm_snap['scale_y']
            d['camera_width'] = CAMERA_WIDTH
            d['camera_height'] = CAMERA_HEIGHT
            d['display_w'] = display_manager.target_width
            d['display_h'] = display_manager.target_height
            return json.dumps(d, separators=(",", ":"))

def compute_palm_normal_3d(world_lm_np: Optional[np.ndarray], handedness_str: str = "Right") -> tuple[tuple[float, float, float], str, bool]:
    """Computes 3D palm normal vector and classifies posture mode."""
    if world_lm_np is None or len(world_lm_np) < 18:
        return (0.0, 0.0, 1.0), "PALM_FACING_CAMERA", False

    v_index = world_lm_np[5, :3] - world_lm_np[0, :3]
    v_pinky = world_lm_np[17, :3] - world_lm_np[0, :3]
    raw_normal = np.cross(v_pinky, v_index) if "Left" in handedness_str else np.cross(v_index, v_pinky)

    norm = np.linalg.norm(raw_normal)
    if norm < 1e-6:
        return (0.0, 0.0, 1.0), "PALM_FACING_CAMERA", False

    n = raw_normal / norm
    nx, ny, nz = float(n[0]), float(n[1]), float(n[2])
    is_flat_down = ny > 0.40

    if is_flat_down and len(world_lm_np) >= 13:
        v_middle = world_lm_np[12, :3] - world_lm_np[9, :3]
        if v_middle[2] > 0.05:
            is_flat_down = False

    if is_flat_down:
        mode = "FLAT_PALM_DOWN"
    elif ny < -0.40:
        mode = "FLAT_PALM_UP"
    elif abs(nx) > 0.70:
        mode = "SIDEWAYS_EDGE"
    else:
        mode = "PALM_FACING_CAMERA"

    return (nx, ny, nz), mode, is_flat_down

def check_finger_curl(lm_np: np.ndarray, world_lm_np: Optional[np.ndarray] = None, is_flat_mode: bool = False) -> tuple[float, float]:
    """Calculates pointing pose extension scores."""
    def cosine_angle(mcp_idx: int, pip_idx: int, tip_idx: int) -> float:
        v1_2d = lm_np[pip_idx, :2] - lm_np[mcp_idx, :2]
        v2_2d = lm_np[tip_idx, :2] - lm_np[pip_idx, :2]
        n1_2d, n2_2d = np.linalg.norm(v1_2d), np.linalg.norm(v2_2d)

        if (n1_2d < 0.035 or n2_2d < 0.035 or is_flat_mode) and world_lm_np is not None:
            v1_3d = world_lm_np[pip_idx, :3] - world_lm_np[mcp_idx, :3]
            v2_3d = world_lm_np[tip_idx, :3] - world_lm_np[pip_idx, :3]
            n1_3d, n2_3d = np.linalg.norm(v1_3d), np.linalg.norm(v2_3d)
            if n1_3d < 1e-6 or n2_3d < 1e-6:
                return 1.0
            return float(np.clip(np.dot(v1_3d, v2_3d) / (n1_3d * n2_3d), -1.0, 1.0))

        if n1_2d < 1e-6 or n2_2d < 1e-6:
            return 1.0
        return float(np.clip(np.dot(v1_2d, v2_2d) / (n1_2d * n2_2d), -1.0, 1.0))

    index_cos = cosine_angle(5, 6, 8)
    index_score = max(0.0, min(1.0, (index_cos - 0.60) / (0.82 - 0.60)))
    pointing_score = 0.0 if math.isnan(index_score) else index_score
    return pointing_score, index_score

def normalize_frame_lighting(small_frame: np.ndarray) -> np.ndarray:
    """Selective adaptive preprocessing: applies CLAHE only under extreme overexposure/glare."""
    try:
        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
        mean_lum = float(np.mean(gray))
        std_lum = float(np.std(gray))
        
        if mean_lum > 180.0 or (mean_lum < 60.0 and std_lum < 30.0):
            lab = cv2.cvtColor(small_frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
            cl = clahe.apply(l)
            limg = cv2.merge((cl, a, b))
            return cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)
        return small_frame
    except Exception:
        return small_frame

class GestureFSM:
    """Dwell-to-click finite state machine."""
    def __init__(self):
        self.state = "MOVE"
        self.dwell_start_ts = 0.0
        self.cooldown_until = 0.0
        self.dwell_origin = None

    def tick(self, now: float, is_pointing: bool, screen_x: float, screen_y: float, dwell_radius: float) -> tuple[str, float]:
        if now < self.cooldown_until or not is_pointing:
            self.state = "MOVE"
            self.dwell_origin = None
            return "MOVE", 0.0

        if self.dwell_origin is None:
            self.dwell_origin = (screen_x, screen_y)
            self.dwell_start_ts = now
            self.state = "DWELL"
            return "DWELL", 0.0

        drift = math.hypot(screen_x - self.dwell_origin[0], screen_y - self.dwell_origin[1])
        if drift > dwell_radius:
            self.dwell_origin = (screen_x, screen_y)
            self.dwell_start_ts = now
            self.state = "DWELL"
            return "DWELL", 0.0

        elapsed = now - self.dwell_start_ts
        progress = min(1.0, elapsed / DWELL_DURATION_S)

        if progress >= 1.0:
            self.state = "CLICK"
            self.cooldown_until = now + 0.8
            self.dwell_origin = None
            return "CLICK", 1.0

        self.state = "DWELL"
        return "DWELL", progress

class GestureProcessor:
    """Interprets MediaPipe landmarks into smoothed screen positions and click gestures."""
    def __init__(self, shared_state: GestureState) -> None:
        self._state = shared_state
        self._last_ts: float = 0.0
        self._fsm = GestureFSM()
        self._cursor_filter = OneEuroFilter(min_cutoff=0.8, beta=0.7, d_cutoff=1.0)
        self._last_raw_lm: Optional[np.ndarray] = None
        self._lm_velocity: Optional[np.ndarray] = None
        self._lm_ema: Optional[np.ndarray] = None
        self._depth_z_ema: Optional[float] = None
        self._pointing_active: bool = False
        self._x_history: deque[tuple[float, float]] = deque()
        self._swipe_cooldown_until: float = 0.0
        self._mouse = _MouseController() if (_PYNPUT_AVAILABLE and ENABLE_SYSTEM_MOUSE) else None
        self._prev_cursor_state: str = "MOVE"
        self._last_screen_x: Optional[int] = None
        self._last_screen_y: Optional[int] = None

    def process(self, lms: Any, world_lms: Optional[Any] = None, handedness: str = "Right") -> None:
        now = time.perf_counter()
        raw_lm_np = np.nan_to_num(np.array([[lm.x, lm.y, getattr(lm, 'z', 0.0)] for lm in lms], dtype=np.float32))
        world_lm_np = np.nan_to_num(np.array([[lm.x, lm.y, getattr(lm, 'z', 0.0)] for lm in world_lms], dtype=np.float32)) if world_lms else None

        _rotation = display_manager.rotation if CAMERA_PHYSICALLY_ROTATED else 0
        if _rotation == 90:
            raw_lm_np[:, :2] = 1.0 - raw_lm_np[:, [1, 0]]
        elif _rotation == -90:
            raw_lm_np[:, :2] = raw_lm_np[:, [1, 0]]
        elif _rotation == 180:
            raw_lm_np[:, 1] = 1.0 - raw_lm_np[:, 1]

        self._run_pipeline(now, raw_lm_np, world_lm_np, handedness)

    def miss(self) -> None:
        now = time.perf_counter()
        if self._last_raw_lm is not None and (now - self._last_ts) < MISS_TOLERANCE_S:
            self._run_pipeline(now, None, None, "Right")
        else:
            self.reset()

    def _run_pipeline(self, now: float, raw_lm_np: Optional[np.ndarray], world_lm_np: Optional[np.ndarray], handedness: str = "Right") -> None:
        is_flat_down = False
        if raw_lm_np is not None:
            palm_normal, hand_pose_mode, is_flat_down = compute_palm_normal_3d(world_lm_np, handedness)
            self._state.hand_pose_mode = hand_pose_mode
            self._state.palm_normal = palm_normal

            if self._last_ts > 0 and self._last_raw_lm is not None:
                dt = now - self._last_ts
                if dt > 0:
                    self._lm_velocity = (raw_lm_np - self._last_raw_lm) / dt
                    palm_vel = np.mean(self._lm_velocity[[0, 5, 17], :2], axis=0)
                    palm_speed = float(np.linalg.norm(palm_vel))

                    d_05 = float(np.hypot(raw_lm_np[5, 0] - raw_lm_np[0, 0], raw_lm_np[5, 1] - raw_lm_np[0, 1]))
                    d_58 = float(np.hypot(raw_lm_np[8, 0] - raw_lm_np[5, 0], raw_lm_np[8, 1] - raw_lm_np[5, 1]))
                    rf_ratio = d_58 / (d_05 + 1e-6)

                    max_alpha = 0.40 if rf_ratio < 0.35 else 0.55
                    alpha_val = float(np.clip(0.12 + (max_alpha - 0.12) * (1.0 - np.exp(-3.0 * palm_speed)), 0.12, max_alpha))
                    alphas = np.full((21, 1), alpha_val, dtype=np.float32)
                else:
                    alphas = 0.5
            else:
                self._lm_velocity = np.zeros_like(raw_lm_np)
                alphas = 1.0

            if self._lm_ema is None or self._lm_ema.shape != raw_lm_np.shape:
                self._lm_ema = raw_lm_np.copy()
            else:
                self._lm_ema = alphas * raw_lm_np + (1.0 - alphas) * self._lm_ema

            self._last_raw_lm = raw_lm_np.copy()
            self._last_ts = now
            pointing_score, index_score = check_finger_curl(raw_lm_np, world_lm_np, is_flat_down)

            if index_score >= 0.50:
                self._pointing_active = True
            elif index_score <= 0.30:
                self._pointing_active = False

            smooth_lm_np = self._lm_ema

            d_05_sm = float(np.hypot(smooth_lm_np[5, 0] - smooth_lm_np[0, 0], smooth_lm_np[5, 1] - smooth_lm_np[0, 1]))
            d_58_sm = float(np.hypot(smooth_lm_np[8, 0] - smooth_lm_np[5, 0], smooth_lm_np[8, 1] - smooth_lm_np[5, 1]))
            rf_ratio_sm = d_58_sm / (d_05_sm + 1e-6)

            if rf_ratio_sm < 0.35 and self._pointing_active:
                v05 = smooth_lm_np[5, :2] - smooth_lm_np[0, :2]
                norm05 = float(np.linalg.norm(v05))
                if norm05 > 1e-6:
                    u05 = v05 / norm05
                    mcp5 = smooth_lm_np[5, :2]
                    tip8 = smooth_lm_np[8, :2]
                    len58 = float(np.linalg.norm(tip8 - mcp5))
                    smooth_lm_np[8, :2] = mcp5 + u05 * len58
                    smooth_lm_np[6, :2] = mcp5 + u05 * (len58 * 0.33)
                    smooth_lm_np[7, :2] = mcp5 + u05 * (len58 * 0.66)
        else:
            if self._last_raw_lm is None or self._lm_velocity is None or self._lm_ema is None:
                self.reset()
                return
            dt_miss = now - self._last_ts
            smooth_lm_np = self._last_raw_lm + self._lm_velocity * dt_miss

        def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
            return max(lo, min(hi, v))

        tip_x, tip_y = float(smooth_lm_np[8, 0]), float(smooth_lm_np[8, 1])
        skel_anchor_x, skel_anchor_y = tip_x, tip_y
        raw_x = tip_x if not (math.isnan(tip_x) or math.isinf(tip_x)) else 0.5
        raw_y = tip_y if not (math.isnan(tip_y) or math.isinf(tip_y)) else 0.5

        cursor_pos = self._cursor_filter(now, np.array([raw_x, raw_y]))
        smooth_x, smooth_y = float(cursor_pos[0]), float(cursor_pos[1])

        dm = display_manager.get_snapshot()
        active_x_min, active_x_max = dm['active_x_min'], dm['active_x_max']
        active_y_min, active_y_max = dm['active_y_min'], dm['active_y_max']
        target_width, target_height = dm['target_width'], dm['target_height']

        def map_to_screen(x, y):
            mapped_x = clamp((x - active_x_min) / (active_x_max - active_x_min)) if (active_x_max - active_x_min) != 0 else 0.5
            mapped_y = clamp((y - active_y_min) / (active_y_max - active_y_min)) if (active_y_max - active_y_min) != 0 else 0.5

            def apply_edge_acceleration(val: float, power: float = 1.3) -> float:
                nx = (val - 0.5) * 2.0
                nx = math.copysign(abs(nx) ** power, nx)
                return (nx / 2.0) + 0.5

            mapped_x = apply_edge_acceleration(mapped_x)
            mapped_y = apply_edge_acceleration(mapped_y)
            sx = max(0, min(target_width - 1, int((1.0 - mapped_x) * target_width)))
            sy = max(0, min(target_height - 1, int(mapped_y * target_height)))
            return sx, sy

        screen_x, screen_y = map_to_screen(smooth_x, smooth_y)
        skel_sx, skel_sy = map_to_screen(skel_anchor_x, skel_anchor_y)

        if self._last_screen_x is not None:
            if math.hypot(screen_x - self._last_screen_x, screen_y - self._last_screen_y) < 2.0:
                screen_x, screen_y = self._last_screen_x, self._last_screen_y
            else:
                self._last_screen_x, self._last_screen_y = screen_x, screen_y
        else:
            self._last_screen_x, self._last_screen_y = screen_x, screen_y

        cursor_state, dwell_progress = self._fsm.tick(now, self._pointing_active, skel_sx, skel_sy, DWELL_RADIUS_PX)

        self._x_history.append((now, smooth_x))
        while self._x_history and now - self._x_history[0][0] > SWIPE_HISTORY_DURATION_S:
            self._x_history.popleft()

        gesture = "NONE"
        if now >= self._swipe_cooldown_until and len(self._x_history) >= 2:
            dt_swipe = self._x_history[-1][0] - self._x_history[0][0]
            if dt_swipe > 0.05:
                dx = self._x_history[-1][1] - self._x_history[0][1]
                velocity = dx / dt_swipe
                if abs(velocity) > SWIPE_VELOCITY_THRESHOLD:
                    gesture = "SWIPE_RIGHT" if velocity < 0 else "SWIPE_LEFT"
                    self._swipe_cooldown_until = now + SWIPE_COOLDOWN_S
                    self._fsm.dwell_origin = None

        publish_lm = smooth_lm_np.tolist()

        if world_lm_np is not None and len(world_lm_np) >= 10:
            m_span = float(np.linalg.norm(world_lm_np[9, :3] - world_lm_np[0, :3]))
            raw_depth_z = m_span / 0.10
        else:
            raw_palm_span = float(np.hypot(smooth_lm_np[9, 0] - smooth_lm_np[0, 0], smooth_lm_np[9, 1] - smooth_lm_np[0, 1]))
            raw_depth_z = raw_palm_span / REFERENCE_SIZE

        if self._depth_z_ema is None:
            self._depth_z_ema = raw_depth_z
        else:
            self._depth_z_ema = 0.08 * raw_depth_z + 0.92 * self._depth_z_ema

        depth_val = float(np.clip(self._depth_z_ema, 0.4, 2.5))

        self._state.skel_anchor_x = float(skel_sx)
        self._state.skel_anchor_y = float(skel_sy)
        self._state.update(
            screen_x, screen_y, cursor_state, gesture,
            hand_detected=True,
            dwell_progress=dwell_progress,
            landmarks=publish_lm,
            hand_size=depth_val,
            depth_z=depth_val,
        )

        if self._mouse is not None:
            self._mouse.position = (screen_x, screen_y)

        if cursor_state == "CLICK" and self._prev_cursor_state != "CLICK":
            if self._mouse is not None and _Button is not None:
                try:
                    self._mouse.position = (screen_x, screen_y)
                    self._mouse.click(_Button.left)
                    log.info("Pynput click injected at (%d, %d)", screen_x, screen_y)
                except Exception as e:
                    log.warning("Pynput click failed: %s", e)
            elif sys.platform != "win32":
                def _inject_xdotool_click(sx: int, sy: int):
                    try:
                        env = os.environ.copy()
                        env.setdefault("DISPLAY", ":0")
                        subprocess.run(["xdotool", "mousemove", str(sx), str(sy), "click", "1"], env=env, capture_output=True, timeout=3)
                    except Exception:
                        pass
                threading.Thread(target=_inject_xdotool_click, args=(screen_x, screen_y), daemon=True).start()

        self._prev_cursor_state = cursor_state

    def reset(self) -> None:
        self._last_raw_lm = None
        self._lm_velocity = None
        self._lm_ema = None
        self._depth_z_ema = None
        self._last_screen_x = None
        self._last_screen_y = None
        self._pointing_active = False
        self._fsm.dwell_origin = None
        self._x_history.clear()
        self._swipe_cooldown_until = 0.0
        self._prev_cursor_state = "MOVE"
        self._state.skel_anchor_x = float(self._state.x)
        self._state.skel_anchor_y = float(self._state.y)
        self._state.update(
            self._state.x, self._state.y, "MOVE", "NONE",
            hand_detected=False,
            dwell_progress=0.0,
            landmarks=[],
            hand_size=1.0,
            depth_z=1.0,
        )

class CameraStream:
    """Grabs camera frames in a dedicated thread using OpenCV."""
    def __init__(self, camera_index: int, width: int, height: int, fps: int, stop_event: threading.Event) -> None:
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.stop_event = stop_event
        self.cap = None
        self.frame = None
        self.ret = False
        self.stopped = False
        self.lock = threading.Lock()

    def start(self) -> bool:
        log.info("Opening webcam (index=%d)...", self.camera_index)
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_V4L2
        self.cap = cv2.VideoCapture(self.camera_index, backend)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        try:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        except Exception:
            pass

        if not self.cap.isOpened():
            log.error("Cannot open webcam index=%d.", self.camera_index)
            return False

        self.ret, frame = self.cap.read()
        if self.ret:
            self.frame = frame.copy()
        threading.Thread(target=self.update, daemon=True).start()
        return True

    def update(self) -> None:
        while not self.stopped and not self.stop_event.is_set():
            if self.cap is not None:
                ret, frame = self.cap.read()
                with self.lock:
                    self.ret = ret
                    if ret:
                        self.frame = frame.copy()
                if not ret:
                    time.sleep(0.01)

    def read(self):
        with self.lock:
            if not self.ret or getattr(self, 'frame', None) is None:
                return False, None
            return self.ret, self.frame.copy()

    def release(self) -> None:
        self.stopped = True
        if self.cap is not None:
            self.cap.release()

def _open_camera(stop_event: threading.Event) -> Optional[CameraStream]:
    stream = CameraStream(CAMERA_INDEX, CAMERA_WIDTH, CAMERA_HEIGHT, TARGET_FPS, stop_event)
    if stream.start():
        return stream
    return None

def _rate_limit(frame_interval: float, last_ts: float) -> None:
    remaining = frame_interval - (time.perf_counter() - last_ts)
    if remaining > 0.001:
        time.sleep(remaining)

def validate_camera_access() -> bool:
    try:
        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            return False
        ret, frame = cap.read()
        cap.release()
        return ret
    except Exception:
        return False

def _camera_loop_tasks(shared_state: GestureState, stop_event: threading.Event) -> None:
    from mediapipe.tasks import python as _mp_python
    from mediapipe.tasks.python import vision as _mp_vision

    _ensure_model()
    base_opts = _mp_python.BaseOptions(model_asset_path=_MODEL_PATH)
    processor = GestureProcessor(shared_state)
    result_queue = queue.Queue(maxsize=1)
    in_flight = False
    in_flight_ts = 0.0

    def _result_callback(result, output_image, timestamp_ms: int):
        nonlocal in_flight
        in_flight = False
        try:
            wl = result.hand_world_landmarks[0] if result.hand_world_landmarks else None
            lm = result.hand_landmarks[0] if result.hand_landmarks else None
            handedness_str = "Right"
            if result.handedness:
                try:
                    handedness_str = result.handedness[0][0].category_name
                except (IndexError, AttributeError):
                    pass
            try:
                while True:
                    result_queue.get_nowait()
            except queue.Empty:
                pass
            result_queue.put_nowait((lm, wl, handedness_str))
        except queue.Full:
            pass

    options = _mp_vision.HandLandmarkerOptions(
        base_options=base_opts,
        running_mode=_mp_vision.RunningMode.LIVE_STREAM,
        num_hands=1,
        min_hand_detection_confidence=DETECT_CONFIDENCE,
        min_hand_presence_confidence=PRESENCE_CONFIDENCE,
        min_tracking_confidence=TRACK_CONFIDENCE,
        result_callback=_result_callback,
    )

    with _mp_vision.HandLandmarker.create_from_options(options) as landmarker:
        cap = None
        reconnect_attempt = 0
        max_reconnect_attempts = 5
        reconnect_delay = 0.2
        frame_interval = 1.0 / 30.0
        last_ts = 0.0
        last_ts_ms = 0
        geometry_poll_ctr = 0
        read_fail_count = 0

        while not stop_event.is_set():
            try:
                if cap is None:
                    cap = _open_camera(stop_event)
                    if cap is None:
                        raise RuntimeError(f"Camera {CAMERA_INDEX} not available")
                    reconnect_attempt = 0
                    read_fail_count = 0
                    with shared_state._lock:
                        shared_state._hand_detected = False
                        shared_state._gesture = "NONE"

                _rate_limit(frame_interval, last_ts)
                ret, frame = cap.read()
                if not ret or frame is None:
                    read_fail_count += 1
                    if read_fail_count > 30:
                        raise RuntimeError("Camera returned consecutive bad frames")
                    continue

                read_fail_count = 0
                last_ts = time.perf_counter()

                geometry_poll_ctr += 1
                if geometry_poll_ctr >= TARGET_FPS * 2:
                    geometry_poll_ctr = 0
                    display_manager.update()

                small_frame = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
                norm_frame = normalize_frame_lighting(small_frame)
                rgb = cv2.cvtColor(norm_frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

                ts_ms = int(time.perf_counter() * 1000)
                if ts_ms <= last_ts_ms:
                    ts_ms = last_ts_ms + 1
                last_ts_ms = ts_ms

                now_ts = time.perf_counter()
                if in_flight and (now_ts - in_flight_ts > 0.150):
                    in_flight = False

                if not in_flight:
                    in_flight = True
                    in_flight_ts = now_ts
                    try:
                        landmarker.detect_async(mp_image, ts_ms)
                    except Exception:
                        in_flight = False

                try:
                    while True:
                        lm, wl, hand_str = result_queue.get_nowait()
                        if lm:
                            try:
                                processor.process(lm, wl, hand_str)
                            except Exception:
                                processor.miss()
                        else:
                            processor.miss()
                except queue.Empty:
                    pass

            except Exception as e:
                log.error(f"Camera error: {e}")
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                reconnect_attempt += 1
                if reconnect_attempt > max_reconnect_attempts:
                    stop_event.set()
                    break
                wait_time = min(1.0, reconnect_delay * (2 ** min(reconnect_attempt - 1, 3)))
                time.sleep(wait_time)

    if cap is not None:
        cap.release()

def camera_loop(shared_state: GestureState, stop_event: threading.Event) -> None:
    _camera_loop_tasks(shared_state, stop_event)

_connected_clients: set = set()

async def ws_handler(websocket) -> None:
    _connected_clients.add(websocket)
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "geometry":
                try:
                    w, h = int(msg["width"]), int(msg["height"])
                    display_manager.set_external_geometry(w, h)
                except (KeyError, TypeError, ValueError):
                    continue
    finally:
        _connected_clients.discard(websocket)

async def broadcast_loop(shared_state: GestureState, stop_event: threading.Event) -> None:
    interval = 1.0 / TARGET_FPS
    loop = asyncio.get_running_loop()
    while not stop_event.is_set():
        start = loop.time()
        if _connected_clients:
            payload = shared_state.to_json()
            await asyncio.gather(
                *[_send_safe(ws, payload) for ws in list(_connected_clients)],
                return_exceptions=True,
            )
        elapsed = loop.time() - start
        await asyncio.sleep(max(0.0, interval - elapsed))

async def _send_safe(ws, payload: str) -> None:
    try:
        await ws.send(payload)
    except Exception:
        pass

async def run_server(shared_state: GestureState, stop_event: threading.Event) -> None:
    log.info("WebSocket server starting on ws://%s:%d", WS_HOST, WS_PORT)
    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        await broadcast_loop(shared_state, stop_event)

def main() -> None:
    log.info("Starting GestureEngine...")
    if not validate_camera_access():
        raise SystemExit("Camera initialization failed")

    shared_state = GestureState()
    stop_event = threading.Event()

    cam_thread = threading.Thread(
        target=camera_loop,
        args=(shared_state, stop_event),
        name="CameraThread",
        daemon=True,
    )
    cam_thread.start()

    try:
        from PyQt5.QtWidgets import QApplication
        import qasync
        from overlay import OverlayWindow
    except ImportError as e:
        log.critical(f"Missing UI dependency: {e}")
        stop_event.set()
        cam_thread.join()
        sys.exit(1)

    if sys.platform != "win32":
        os.environ.setdefault("QT_XCB_NATIVE_PAINTING", "1")
        os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "0")
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

    app = QApplication(sys.argv)
    app.setApplicationName("GestureEngine")
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    def geometry_changed(w: int, h: int) -> None:
        display_manager.set_external_geometry(w, h)

    window = OverlayWindow(shared_state, geometry_callback=geometry_changed)
    window.show()

    loop.create_task(run_server(shared_state, stop_event))

    try:
        with loop:
            loop.run_forever()
    except KeyboardInterrupt:
        log.info("Shutdown requested.")
    finally:
        stop_event.set()
        cam_thread.join(timeout=3.0)
        log.info("Engine stopped cleanly.")

if __name__ == "__main__":
    main()
