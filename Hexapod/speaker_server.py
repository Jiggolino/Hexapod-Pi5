import socket
import subprocess

def speak(text):
    # Use the same command that worked for your I2S speaker
    # Adding -p 20 for a slightly more robotic tone
    cmd = f'espeak-ng -p 20 "{text}" --stdout | aplay -D plug:default'
    subprocess.run(cmd, shell=True)

def start_server(port=5001):
    # Create a TCP/IP socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Allow the port to be reused immediately after closing
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', port))
        s.listen()
        print(f"Clanker listening on port {port}...")

        while True:
            conn, addr = s.accept()
            with conn:
                data = conn.recv(1024)
                if not data:
                    break
                
                message = data.decode('utf-8').strip()
                if message:
                    print(f"Received: {message}")
                    speak(message)

if __name__ == "__main__":
    try:
        start_server()
    except KeyboardInterrupt:
        print("\nClanker shutting down.")
