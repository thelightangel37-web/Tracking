# Tracking

Raspberry Pi Gesture Engine & Transparent Overlay Kiosk

A lightweight, real-time hand gesture tracking and visual feedback overlay system engineered for interactive kiosks (Raspberry Pi or desktop). The project runs as two cooperating Python processes: a headless gesture engine that performs webcam capture and hand landmark extraction, and a transparent PyQt overlay that renders a skeleton and dwell-to-click UI over the OS display.

Key features

- Real-time 21-landmark hand tracking (MediaPipe HandLandmarker)
- Low-latency WebSocket JSON stream between processes
- Transparent, click-through PyQt5 overlay with isotropic auto-scaling
- Dwell-to-click FSA with ghost protection and cooldown
- Lighting normalization (CLAHE, gamma) and tremor filtering (OneEuro / EMA)
- Designed to run on Raspberry Pi and standard Linux desktops (X11)

Requirements

- Python 3.8+
- OpenCV (cv2)
- MediaPipe Tasks API (HandLandmarker)
- PyQt5 and qasync
- websockets
- pynput / evdev / xdotool (for OS input injection, platform dependent)

Installation

1. Create a virtual environment and activate it:

   python -m venv .venv
   source .venv/bin/activate

2. Install dependencies (example):

   pip install -r requirements.txt

Note: Requirements can be tailored for a specific platform (Raspberry Pi builds may need system packages).

Quick start

- Start the gesture engine (webcam capture + WebSocket server):

  python gesture_engine.py

- Start the overlay (WebSocket client + transparent UI):

  python overlay.py

Both components support CLI configuration flags and environment variables for camera index, resolution, WebSocket host/port, and tuning parameters (see the system_architecture_report.md for defaults and configuration reference).

Configuration

Core parameters and recommended defaults are documented in system_architecture_report.md. Important ones include:

- CAMERA_INDEX: 0
- CAMERA_WIDTH / CAMERA_HEIGHT: 640 x 480
- TARGET_FPS: 60
- DWELL_DURATION_S: 1.2
- DWELL_RADIUS_PX: 48
- WS_HOST / PORT: localhost:8765

Project layout

- gesture_engine.py — headless tracking engine, MediaPipe integration, WebSocket server
- overlay.py — transparent PyQt5 overlay and WebSocket client
- system_architecture_report.md — design, algorithm, and configuration reference

Contributing

Contributions welcome. Please open issues for bugs or feature requests and create small, focused pull requests. Include platform and hardware details (Raspberry Pi model, OS version, camera model) when reporting issues.

License

This repository does not include a license file by default. If you want to adopt a license, add a LICENSE file (e.g., MIT) and update this README.

Contact

Open issues or contact the maintainer via the repository's GitHub profile.
