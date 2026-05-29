#!/usr/bin/env python3
import argparse
import serial
import socket
import threading
import time
import sys
import subprocess
import os
import glob

# ─── GStreamer Command ──────────────────────────────────────────────────────
GSTREAMER_CMD = [
    "gst-launch-1.0", "libcamerasrc", "!",
    "video/x-raw,format=I420,width=1920,height=1080,framerate=30/1", "!",
    "x264enc", "tune=zerolatency", "bitrate=5000", "speed-preset=ultrafast", "!",
    "rtph264pay", "config-interval=1", "pt=96", "!",
    "udpsink", "host=100.84.75.117", "port=5005"
]

# ─── Audio Playback ────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "plug:default")
MP3_DIR = None  # Set by main() if --mp3-dir provided
audio_lock = threading.Lock()

def get_mp3_file():
    """Find first .mp3 in specified or fallback directories."""
    search_dirs = [MP3_DIR] if MP3_DIR else []
    search_dirs.extend([
        SCRIPT_DIR,
        os.path.expanduser("~/Hexapod"),
        "/home/clanker/Hexapod",
        "/root/Hexapod",
        "/tmp/Hexapod",
    ])
    for d in search_dirs:
        if d and os.path.isdir(d):
            mp3_files = sorted(glob.glob(os.path.join(d, "*.mp3")))
            if mp3_files:
                path = mp3_files[0]
                print(f"Found MP3: {path}")
                return path
    return None

def _run_audio(cmd, label):
    """Run an audio shell pipeline serialized through audio_lock.
    Captures output so failures are visible instead of silent."""
    with audio_lock:
        try:
            result = subprocess.run(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                err = result.stderr.decode(errors="ignore").strip().splitlines()[-3:]
                print(f"WARNING {label} failed (rc={result.returncode}): {' | '.join(err)}")
        except Exception as e:
            print(f"WARNING {label} error: {e}")

def play_audio():
    mp3_file = get_mp3_file()
    if not mp3_file:
        print("WARNING No MP3 files found")
        return
    print(f"Playing: {os.path.basename(mp3_file)}")
    cmd = (
        f'ffmpeg -nostdin -loglevel error -i "{mp3_file}" '
        f'-f s16le -acodec pcm_s16le -ar 44100 -ac 2 - '
        f'| aplay -q -f S16_LE -r 44100 -c 2 -D {AUDIO_DEVICE}'
    )
    threading.Thread(target=_run_audio, args=(cmd, "MP3 playback"), daemon=True).start()

def speak(text):
    safe = text.replace('"', '\\"')
    cmd = f'espeak-ng -p 20 "{safe}" --stdout | aplay -q -D {AUDIO_DEVICE}'
    threading.Thread(target=_run_audio, args=(cmd, "TTS"), daemon=True).start()

# ─── Speaker (TTS) Server ──────────────────────────────────────────────────
def speaker_server_thread(port=5001):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', port))
        s.listen()
        print(f"Clanker speaker listening on port {port}")
    except Exception as e:
        print(f"Speaker server bind error: {e}")
        return
    while True:
        try:
            conn, addr = s.accept()
            with conn:
                data = conn.recv(4096)
                if not data:
                    continue
                message = data.decode('utf-8', errors='ignore').strip()
                if message:
                    print(f"Received TTS from {addr[0]}: {message}")
                    speak(message)
        except Exception as e:
            print(f"Speaker server error: {e}")
            time.sleep(0.5)

# ─── Battery Monitoring Logic ──────────────────────────────────────────────
def battery_monitor_thread(ser):
    print("Battery monitor started (Threshold: 6.6V)")
    while True:
        try:
            ser.write(b"/BATTERY/V\n")
            time.sleep(1.0)
        except Exception as e:
            print(f"Battery request error: {e}")
            time.sleep(2)

def parse_battery_response(data_str):
    if "/BATTERY/V/" in data_str:
        try:
            parts = data_str.split('/')
            voltage = float(parts[3])
            return voltage
        except (IndexError, ValueError):
            return None
    return None

# ─── Threading Logic ───────────────────────────────────────────────────────

def parse_controller_input(line):
    if line.startswith("/CONTROLL/"):
        try:
            parts = line.replace("/CONTROLL/", "").split(",")
            if len(parts) >= 7:
                face_buttons = int(parts[6])
                return face_buttons
        except (ValueError, IndexError):
            pass
    return None

def serial_to_socket(ser, client_sock):
    buffer = ""
    try:
        while True:
            if ser.in_waiting > 0:
                raw_data = ser.read(ser.in_waiting)
                try:
                    decoded = raw_data.decode('ascii', errors='ignore')
                    buffer += decoded
                    if "\n" in buffer:
                        lines = buffer.split("\n")
                        for line in lines[:-1]:
                            line = line.strip()
                            volts = parse_battery_response(line)
                            if volts is not None:
                                print(f"Current Voltage: {volts}V")
                                if 0.5 < volts <= 6.6:
                                    print("WARNING LOW BATTERY! Shutting down now...")
                                    os.system("sudo shutdown -h now")
                            # Note: Button detection now happens in socket_to_serial (where packets originate)
                            pass
                        buffer = lines[-1]
                except:
                    pass
                client_sock.sendall(raw_data)
            else:
                time.sleep(0.001)
    except: pass

def socket_to_serial(client_sock, ser):
    buffer = ""
    prev_face_buttons = 0
    try:
        while True:
            data = client_sock.recv(1024)
            if not data: break

            # Parse button presses from incoming socket data
            buffer += data.decode('ascii', errors='ignore')
            if "\n" in buffer:
                lines = buffer.split("\n")
                for line in lines[:-1]:
                    line = line.strip()
                    face = parse_controller_input(line)
                    if face is not None:
                        y_pressed = (face & 8) != 0
                        prev_y_pressed = (prev_face_buttons & 8) != 0
                        if y_pressed and not prev_y_pressed:
                            print("Y button PRESSED - play_audio()")
                            play_audio()
                        prev_face_buttons = face
                buffer = lines[-1]

            # Forward to serial
            ser.write(data)
    except: pass

def start_video_stream():
    print("Starting GStreamer Video Stream...")
    return subprocess.Popen(GSTREAMER_CMD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_video_stream(process):
    if process:
        print("Stopping GStreamer Video Stream...")
        process.terminate()

def disarm_hexapod(ser):
    try:
        # Send R1 pressed (trigger_buttons bit4 = 16) to trigger disarm in firmware
        # Format: /CONTROLL/rx,ry,lx,ly,dpad_x,dpad_y,face,stick,trigger,back
        ser.write(b"/CONTROLL/0,0,0,0,0,0,0,0,16,0\n")
        time.sleep(0.1)
        # Release all buttons
        ser.write(b"/CONTROLL/0,0,0,0,0,0,0,0,0,0\n")
        print("Controller disconnected - hexapod disarmed (R1 sent).")
    except Exception as e:
        print(f"Disarm error: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/serial0")
    parser.add_argument("--baud", default=921600, type=int)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--netport", default=5000, type=int)
    parser.add_argument("--mp3-dir", help="Directory containing MP3 files (overrides search)")
    parser.add_argument("--audio-device", default="plug:default", help="ALSA audio device")
    args = parser.parse_args()

    # Override globals if provided
    global AUDIO_DEVICE, MP3_DIR
    AUDIO_DEVICE = args.audio_device
    if args.mp3_dir:
        MP3_DIR = args.mp3_dir

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except Exception as e:
        print(f"Serial error: {e}"); sys.exit(1)

    threading.Thread(target=battery_monitor_thread, args=(ser,), daemon=True).start()
    threading.Thread(target=speaker_server_thread, daemon=True).start()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((args.listen, args.netport))
    server_sock.listen(1)

    stream_process = None

    try:
        while True:
            print("\nWaiting for connection...")
            client_sock, addr = server_sock.accept()
            print(f"Connected by {addr}")

            stream_process = start_video_stream()

            t1 = threading.Thread(target=serial_to_socket, args=(ser, client_sock), daemon=True)
            t2 = threading.Thread(target=socket_to_serial, args=(client_sock, ser), daemon=True)

            t1.start()
            t2.start()

            while t1.is_alive() and t2.is_alive():
                time.sleep(0.5)

            print("Connection lost.")
            stop_video_stream(stream_process)
            client_sock.close()
            disarm_hexapod(ser)
    except KeyboardInterrupt:
        print("\nExit.")
    finally:
        ser.close()
        server_sock.close()

if __name__ == "__main__":
    main()
