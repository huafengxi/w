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

def transform_and_calculate_state(raw_state):
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
        report_timestamp_ms = raw_state.get('report_time', 0)

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
    """
    This function runs in a dedicated thread for each client. It periodically
    watches the state file, calculates the state, and sends it to the client.
    """
    logging.info(f"Client connected: {address}")
    
    my_last_mtime = 0
    my_last_processed_state = {}

    try:
        while True:
            new_mtime = watch_file(STATE_FILE_PATH, my_last_mtime)
            file_changed = new_mtime != my_last_mtime
            
            if file_changed:
                my_last_mtime = new_mtime
                if new_mtime == 0: # File disappeared or error
                    my_last_processed_state = {}
                else:
                    try:
                        with open(STATE_FILE_PATH, 'r') as f:
                            content = f.read()
                            my_last_processed_state = json.loads(content) if content else {}
                    except (FileNotFoundError, json.JSONDecodeError) as e:
                        logging.warning(f"Error reading/parsing {STATE_FILE_PATH} for {address}: {e}")
                        my_last_processed_state = {}

            # If the file changed or we have a playing state, send an update
            if file_changed or (my_last_processed_state and my_last_processed_state.get('state') == 'playing'):
                message_to_send = transform_and_calculate_state(my_last_processed_state)
                encoded_message = (json.dumps(message_to_send) + '\n').encode('utf-8')
                client_socket.sendall(encoded_message)
            
            # Short sleep for polling interval
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
