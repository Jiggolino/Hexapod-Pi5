#!/usr/bin/env python3
import argparse
import serial
import socket
import threading
import time
import sys
import subprocess
import os

# ─── GStreamer Command ──────────────────────────────────────────────────────
GSTREAMER_CMD = [
    "gst-launch-1.0", "libcamerasrc", "!", 
    "video/x-raw,format=I420,width=1920,height=1080,framerate=30/1", "!", 
    "x264enc", "tune=zerolatency", "bitrate=5000", "speed-preset=ultrafast", "!", 
    "rtph264pay", "config-interval=1", "pt=96", "!", 
    "udpsink", "host=100.84.75.117", "port=5005"
]

# ─── Battery Monitoring Logic ──────────────────────────────────────────────
def battery_monitor_thread(ser):
    print("🔋 Battery monitor started (Threshold: 6.6V)")
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
                            volts = parse_battery_response(line.strip())
                            if volts is not None:
                                print(f"Current Voltage: {volts}V")
                                if 0.5 < volts <= 6.6:
                                    print("⚠️ LOW BATTERY! Shutting down now...")
                                    os.system("sudo shutdown -h now")
                        buffer = lines[-1]
                except:
                    pass
                client_sock.sendall(raw_data)
            else:
                time.sleep(0.001)
    except: pass

def socket_to_serial(client_sock, ser):
    try:
        while True:
            data = client_sock.recv(1024)
            if not data: break
            ser.write(data)
    except: pass

def start_video_stream():
    print("🚀 Starting GStreamer Video Stream...")
    return subprocess.Popen(GSTREAMER_CMD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_video_stream(process):
    if process:
        print("🛑 Stopping GStreamer Video Stream...")
        process.terminate()

def disarm_hexapod(ser):
    try:
        # Send R1 pressed (trigger_buttons bit4 = 16) to trigger disarm in firmware
        # Format: /CONTROLL/rx,ry,lx,ly,dpad_x,dpad_y,face,stick,trigger,back
        ser.write(b"/CONTROLL/0,0,0,0,0,0,0,0,16,0\n")
        time.sleep(0.1)
        # Release all buttons
        ser.write(b"/CONTROLL/0,0,0,0,0,0,0,0,0,0\n")
        print("🛑 Controller disconnected — hexapod disarmed (R1 sent).")
    except Exception as e:
        print(f"Disarm error: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/serial0")
    parser.add_argument("--baud", default=921600, type=int)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--netport", default=5000, type=int)
    args = parser.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except Exception as e:
        print(f"Serial error: {e}"); sys.exit(1)

    threading.Thread(target=battery_monitor_thread, args=(ser,), daemon=True).start()

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
