# HOME OS Native Linux Agent (`homeos-linux-agent`)

Native Linux companion daemon for **HOME OS**, providing:
- `/proc` & `os.statvfs` telemetry (CPU load, RAM, disk, battery, hostname, OS kernel)
- Dynamic X11 / Wayland / Headless display & input capability probing
- PipeWire (`wpctl`) / PulseAudio (`pactl`) / ALSA (`amixer`) volume control + MPRIS2 media control
- Root-jailed file browser & SHA-256 verified bidirectional file transfer
- Authorized process inspection & Desktop Entry (`gtk-launch`) launcher
- Signed Ed25519/SHA-256 update manifest verification
- `0600` AES-256-GCM local credential vault (`~/.local/share/homeos-linux-agent/`)
