# Rigorous Implementation Plan - Lighting Normalization & Pointing-Gesture Stability

This plan provides fundamental, high-impact fixes for the two major issues:
1. **Bright Light Glare & Skeleton Twitching**: Implementing hardware/software glare suppression (Bilateral Filtering + LAB CLAHE + specular highlight masking) and confidence-gated adaptive temporal filtering to eliminate joint twitching.
2. **Pointing Gesture Collapse, Stretching & Camera Crash**: Fixing 2D foreshortening ray projection, sanitizing 3D MediaPipe world landmarks, isolating C++ runtime exceptions, and adding Point-Mode Skeleton Decoupling in `overlay.py`.

---

## Technical Analysis & Rigorous Solutions

### Issue 1: Bright Light Glare & Skeleton Twitching

#### Root Cause
Under bright overhead lighting or direct glare, camera sensor pixels saturate to white (`RGB 255, 255, 255`), washing out skin texture and joint contrast. MediaPipe's landmark detector loses confidence and outputs noisy, rapidly fluctuating joint coordinates every frame, producing visible "twitching."

#### Rigorous Fix
1. **Multi-Stage Glare & Contrast Filtering (`normalize_frame_lighting`)**:
   - **Bilateral Glare Smoothing**: Apply `cv2.bilateralFilter` to remove high-frequency specular glare spots while keeping skin/edge boundaries sharp.
   - **LAB CLAHE Contrast Normalization**: Equalize the `L` channel (`clipLimit=3.0`, `tileGridSize=(8,8)`) to extract landmark features under harsh lighting.
   - **Highlight Masking & Suppress Overexposure**: Detect specular highlight pixels (`V > 215` in HSV space) and blend them with local neighborhood luminance.
2. **Adaptive Confidence-Gated Temporal Filtering**:
   - Calculate frame-to-frame landmark jitter energy $E_j = \|\text{raw\_lm} - \text{prev\_lm}\|$.
   - When jitter energy is high (due to bright light noise), dynamically lower the EMA alpha ($\alpha \to 0.05$) and tighten OneEuro filter cutoff frequency ($min\_cutoff \to 0.4$), locking the skeleton solid and eliminating twitching.

---

### Issue 2: Pointing Gesture Collapse, Stretching & Camera Crash

#### Root Cause
1. **Ray Projection Vector Flipping**: When pointing index finger directly at the camera screen, 2D distance between Index Base (LM 5) and Index Tip (LM 8) collapses ($\le 0.025$). The vector direction $v_{58} = LM_8 - LM_5$ becomes random 2D noise, causing the projected cursor ray to shoot wildly across the screen.
2. **MediaPipe 3D World Landmark Instability & C++ Exception Crash**: When pointing straight at the lens, single-camera depth estimation produces extreme $Z$-depth values or `NaN`/`Inf` in `world_landmarks`. If passed unsanitized, MediaPipe's C++ runtime throws an internal exception that invalidates the landmarker session and tears down the camera, causing the 4-5s freeze.
3. **Visual Crumbling in `overlay.py`**: Index finger joints (5, 6, 7, 8) collapse onto the same screen pixel coordinates, rendering overlapping stacked dots and cross-hatched connection line artifacts.

#### Rigorous Fix
1. **Pointing Foreshortening Lock (`gesture_engine.py`)**:
   - Compute foreshortening ratio $R_f = \frac{\|LM_8 - LM_5\|_{2D}}{\|LM_5 - LM_0\|_{2D}}$.
   - If $R_f < 0.35$ (pointing towards screen), lock ray projection direction strictly to the stable palm axis $v_{05} = LM_5 - LM_0$ (Wrist to Index Base) and suppress $v_{58}$ noise. This completely prevents ray projection from stretching across the screen!
2. **Sanitize 3D World Landmarks & Hardened Session Recovery**:
   - Apply `np.nan_to_num` to all 21 normalized and 3D world landmarks before processing.
   - If `world_landmarks` has invalid depth values, fallback to 2D projection mode without re-instantiating `HandLandmarker`.
3. **Point-Mode Skeleton Rendering (`overlay.py`)**:
   - In `_draw_skeleton()`, when index finger pointing is active and joints are foreshortened:
     - Collapse intermediate finger joints (6, 7) into a single streamlined bone segment ($LM_5 \to LM_8$).
     - Render a clean **Focus Cursor Ring** at Index Tip (LM 8) and suppress overlapping internal joint dots.

---

## Proposed Changes

### Gesture Engine Core

#### [MODIFY] [gesture_engine.py](file:///c:/Users/kito/Desktop/A%20to%20Z/Project_Designs/Skeleton/gesture_engine.py)

- **Enhanced Lighting Filter**: Implement Bilateral Filtering + LAB CLAHE + specular highlight suppression in `normalize_frame_lighting()`.
- **Foreshortening Lock**: Implement $R_f$ ratio detection in `_run_pipeline()` to lock projection vectors to palm axis $v_{05}$ during pointing gestures towards the screen.
- **Landmark Sanitization**: Sanitize all landmarks with `np.nan_to_num()` and add adaptive jitter-gated EMA smoothing.

---

### UI & Skeleton Overlay

#### [MODIFY] [overlay.py](file:///c:/Users/kito/Desktop/A%20to%20Z/Project_Designs/Skeleton/overlay.py)

- **Point-Mode Skeleton Decoupling**: Update `_draw_skeleton()` to streamline foreshortened pointing finger joints, suppress stacked joint dots during screen-pointing gestures, and draw a crisp focus ring.

---

## Verification Plan

### Automated / Syntax Verification
- Run Python compilation checks (`python -m py_compile gesture_engine.py overlay.py`) to verify zero syntax errors.

### Manual Verification
- **Harsh Lighting Test**: Expose camera to direct bright light/glare. Verify skeleton joints remain completely steady without twitching.
- **Screen Pointing Test**: Hold index finger pointing gesture directly at the screen lens. Verify cursor ray does NOT stretch or jump, skeleton renders cleanly without crumbling, and camera does NOT crash or freeze.
