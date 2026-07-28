# Tracking

Raspberry Pi Gesture Engine & Transparent Overlay Kiosk

A lightweight, real-time hand gesture tracking and visual feedback overlay system engineered for interactive kiosks (Raspberry Pi or desktop). The project runs as two cooperating Python processes: a headless gesture engine that captures webcam frames, runs MediaPipe hand landmark detection, and streams JSON over a WebSocket; and a transparent overlay client that receives the stream and renders a click-through PyQt5 overlay.

Key features

- Real-time 21-landmark hand tracking (MediaPipe HandLandmarker)
- Low-latency WebSocket JSON stream between processes
- Transparent, click-through PyQt5 overlay with isotropic auto-scaling
- Dwell-to-click finite state automaton (FSA) with ghost protection and cooldown
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

1. Create and activate a virtual environment:

   python -m venv .venv
   source .venv/bin/activate

2. Install Python dependencies:

   pip install -r requirements.txt

Note: Requirements can be tailored for a specific platform (Raspberry Pi builds may need additional system packages such as libatlas, libjpeg-dev, ffmpeg, or platform-specific wheels).

Quick start

- Start the gesture engine (webcam capture + WebSocket server):

  python gesture_engine.py

- Start the overlay (WebSocket client + transparent UI):

  python overlay.py

Both components support CLI configuration flags and environment variables for camera index, resolution, WebSocket host/port, and tuning parameters. See system_architecture_report.md for default values and more details.

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

Contributions welcome. Please open issues for bugs or feature requests and create small, focused pull requests. When opening issues or PRs, include platform and hardware details (Raspberry Pi model, OS version, camera model). If you plan to contribute code, follow these guidelines:

- Fork the repository and create a feature branch for your changes.
- Keep changes small and focused; include tests where appropriate.
- Document runtime requirements and configuration changes.

License

This repository does not include a license file by default. If you want to adopt a license, add a LICENSE file (for example, an MIT license) and update this README to reference it.

Contact

Open issues or contact the maintainer via the repository's GitHub profile.
