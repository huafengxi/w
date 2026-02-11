#!/usr/bin/env python3
#
# timestamp-server.py
#
# This is a multi-threaded TCP server. Each client connection is handled
# in its own thread, which periodically sends the real-time calculated
# playback state by watching the state file directly.
#

import json
import logging
import os
import socket
import threading
import time
from urllib.parse import urlparse

# --- Configuration ---
HOST = '0.0.0.0'
PORT = 23554
STATE_FILE_PATH = './playing-state.json'

# --- Globals ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def transform_and_calculate_state(raw_state, mtime_ms):
    """
    Transforms raw state and calculates the real-time currentTime.
    Returns the final dictionary to be broadcast.
    """
    if not raw_state or 'file' not in raw_state:
        return {}
        
    try:
        player_state = 0 if raw_state.get('state') == 'playing' else 1
        path = urlparse(raw_state.get('file', '')).path
        
        current_state = {
            'path': path,
            'playerState': player_state,
        }

        reported_time = raw_state.get('time', 0)
        report_timestamp_ms = mtime_ms

        if current_state['playerState'] == 0: # playing
            report_timestamp_sec = report_timestamp_ms / 1000.0
            elapsed = time.time() - report_timestamp_sec
            calculated_time = reported_time + elapsed
            current_state['currentTime'] = calculated_time
        else: # paused
            current_state['currentTime'] = reported_time
        
        return current_state

    except Exception as e:
        logging.error(f"Error in transform_and_calculate_state: {e}")
        return {}

def watch_file(path, last_mtime):
    """
    Checks a file's modification time.
    Returns the new modification time. Returns 0 if the file doesn't exist.
    """
    if not os.path.exists(path):
        return 0  # Return 0 to indicate file doesn't exist

    try:
        return os.path.getmtime(path)
    except FileNotFoundError:
        # Race condition: file deleted between os.path.exists and os.path.getmtime
        return 0

def handle_client(client_socket, address):
    logging.info(f"Client connected: {address}")
    
    my_last_mtime = 0
    raw_state = {}
    last_broadcast_time = 0
    BROADCAST_INTERVAL = 1.0 # seconds

    try:
        while True:
            current_time = time.time()
            new_mtime = watch_file(STATE_FILE_PATH, my_last_mtime)

            file_exists = new_mtime != 0
            is_stale = file_exists and (current_time - new_mtime > 2)
            file_changed_now = (new_mtime != my_last_mtime)

            # Determine the current state (from file or mock)
            if not file_exists or is_stale:
                raw_state = {'state': 'paused', 'file': ''}
                my_last_mtime = new_mtime
            elif file_changed_now:
                my_last_mtime = new_mtime
                try:
                    with open(STATE_FILE_PATH, 'r') as f:
                        content = f.read()
                        raw_state = json.loads(content) if content else {'state': 'paused', 'file': ''}
                except (FileNotFoundError, json.JSONDecodeError) as e:
                    logging.warning(f"Error reading/parsing {STATE_FILE_PATH} for {address}: {e}")
                    raw_state = {'state': 'paused', 'file': ''}

            # Now, decide whether to broadcast based on time or file change
            time_for_periodic_update = (current_time - last_broadcast_time) >= BROADCAST_INTERVAL
            
            if file_changed_now or time_for_periodic_update:
                message_to_send = transform_and_calculate_state(raw_state, int(my_last_mtime * 1000))
                encoded_message = (json.dumps(message_to_send) + '\n').encode('utf-8')
                client_socket.sendall(encoded_message)
                last_broadcast_time = current_time

            time.sleep(0.1)

    except (BrokenPipeError, ConnectionResetError):
        logging.warning(f"Connection lost with {address}")
    finally:
        logging.info(f"Closing connection with {address}")
        client_socket.close()
def main():
    """
    Main function to set up the server and accept new connections.
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen()
    logging.info(f"Server listening on {HOST}:{PORT}")

    try:
        while True:
            client_socket, address = server_socket.accept()
            client_thread = threading.Thread(target=handle_client, args=(client_socket, address), daemon=True)
            client_thread.start()
    except KeyboardInterrupt:
        logging.info("Server shutting down.")
    finally:
        server_socket.close()

if __name__ == "__main__":
    main()
