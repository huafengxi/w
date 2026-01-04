#!/usr/bin/env python3
#
# timestamp-server.py
#
# This is a multi-threaded TCP server. Each client connection is handled
# in its own thread, which periodically sends the real-time calculated
# playback state.
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

        # Get reported_time and report_timestamp_ms from raw_state for calculation
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

def handle_client(client_socket, address):
    """
    This function runs in a dedicated thread for each client. It periodically
    reads the state file, calculates the state, and sends it to the client.
    """
    logging.info(f"Client connected: {address}")
    
    try:
        while True:
            # Read the state file
            try:
                with open(STATE_FILE_PATH, 'r') as f:
                    content = f.read()
                    raw_state = json.loads(content) if content else {}
            except (FileNotFoundError, json.JSONDecodeError):
                raw_state = {}

            # Transform and calculate the state
            message_to_send = transform_and_calculate_state(raw_state)
            encoded_message = (json.dumps(message_to_send) + '\n').encode('utf-8')
            
            # Send the message to this specific client
            client_socket.sendall(encoded_message)
            
            # Wait for the next cycle
            time.sleep(1)

    except (BrokenPipeError, ConnectionResetError):
        logging.warning(f"Connection lost with {address}")
    finally:
        logging.info(f"Closing connection with {address}")
        client_socket.close()

def main():
    """
    Main function to set up the server and accept new connections.
    """
    # Set up the main server socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen()
    logging.info(f"Server listening on {HOST}:{PORT}")

    try:
        while True:
            # Accept new connections
            client_socket, address = server_socket.accept()
            # Spawn a new thread for each client to handle communication
            client_thread = threading.Thread(target=handle_client, args=(client_socket, address), daemon=True)
            client_thread.start()
    except KeyboardInterrupt:
        logging.info("Server shutting down.")
    finally:
        server_socket.close()

if __name__ == "__main__":
    main()
