#!/usr/bin/env python3
#
# timestamp-server.py
#
# This server monitors playing-state.json and broadcasts the playback
# state to all connected plain TCP socket clients.
#

import asyncio
import json
import logging
import os
from urllib.parse import urlparse

# --- Configuration ---
HOST = '0.0.0.0'
PORT = 23554
STATE_FILE_PATH = './playing-state.json'

# --- Globals ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
connected_clients = set()  # Will store asyncio.StreamWriter objects
last_known_state = {}
last_file_mtime = 0
# Lock to protect access to the shared state
state_lock = asyncio.Lock()

def transform_state(raw_state):
    """
    Transforms the state from playing-state.json into the desired broadcast format.
    """
    if not raw_state or 'file' not in raw_state:
        return None
    try:
        parsed_url = urlparse(raw_state.get('file', ''))
        path = parsed_url.path
        player_state = 0 if raw_state.get('state') == 'playing' else 1
        return {
            'path': path,
            'currentTime': raw_state.get('time', 0),
            'playerState': player_state,
        }
    except Exception as e:
        logging.error(f"Error transforming state: {e}")
        return None

async def broadcast_state():
    """
    Broadcasts the current state to all connected clients.
    """
    async with state_lock:
        if not last_known_state:
            message_to_send = json.dumps({}) + '\n' # Send empty object for no state
        else:
            message_to_send = json.dumps(last_known_state) + '\n'

    if not connected_clients:
        return

    encoded_message = message_to_send.encode('utf-8')
    disconnected_clients = set()
    for writer in connected_clients:
        if writer.is_closing():
            disconnected_clients.add(writer)
            continue
        try:
            writer.write(encoded_message)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            logging.warning(f"Client disconnected: {writer.get_extra_info('peername')}")
            disconnected_clients.add(writer)

    # Clean up disconnected clients outside the iteration
    for client in disconnected_clients:
        connected_clients.discard(client)

async def file_watcher():
    """
    Watches for changes in the state file, updates the global state, and broadcasts it.
    """
    global last_known_state, last_file_mtime
    while True:
        try:
            if os.path.exists(STATE_FILE_PATH):
                current_mtime = os.path.getmtime(STATE_FILE_PATH)
                if current_mtime != last_file_mtime:
                    last_file_mtime = current_mtime
                    with open(STATE_FILE_PATH, 'r') as f:
                        content = f.read()
                        raw_state = {} # Default to empty if content is empty
                        if content:
                            try:
                                raw_state = json.loads(content)
                            except json.JSONDecodeError:
                                logging.warning(f"Malformed JSON in {STATE_FILE_PATH}, treating as empty.")
                                # If malformed, treat as empty state
                                pass
                    
                    new_state = transform_state(raw_state)
                    # Use a sentinel for truly empty state vs. transformation failure
                    if new_state is None and raw_state: # If raw_state was not empty but transform failed
                        logging.warning("Transformation failed for non-empty raw_state, treating as empty.")
                        new_state = {} # Treat as empty state to trigger broadcast
                    elif new_state is None: # If raw_state was empty or transform explicitly returned None
                        new_state = {} # Treat as empty state

                    async with state_lock:
                        if new_state != last_known_state:
                            logging.info(f"State updated from file: {new_state}")
                            last_known_state = new_state
                            await broadcast_state() # Broadcast immediately on change
            else:
                # If file does not exist, clear the last known state
                async with state_lock:
                    if last_known_state: # Only if it had a state previously
                        logging.info("State file disappeared, clearing state.")
                        last_known_state = {}
                        await broadcast_state() # Broadcast empty state

        except (json.JSONDecodeError, FileNotFoundError) as e:
            logging.warning(f"Could not read or parse state file ({e}), clearing state.")
            # If file becomes unreadable, clear state
            async with state_lock:
                if last_known_state:
                    last_known_state = {}
                    await broadcast_state()
        except Exception as e:
            logging.error(f"Error in file_watcher: {e}")
        
        await asyncio.sleep(0.5) # Check for file changes frequently

async def connection_handler(reader, writer):
    """
    Handles a new TCP socket client connection.
    """
    peername = writer.get_extra_info('peername')
    logging.info(f"Client connected: {peername}")
    connected_clients.add(writer)
    try:
        # Send the latest state immediately upon connection
        async with state_lock:
            if last_known_state:
                initial_message = json.dumps(last_known_state) + '\n'
                writer.write(initial_message.encode('utf-8'))
                await writer.drain()
        
        # Keep connection open and wait for client to disconnect
        while not reader.at_eof():
            await reader.read(1024)
            
    except (ConnectionResetError, BrokenPipeError):
        logging.warning(f"Connection lost with {peername}")
    finally:
        logging.info(f"Closing connection with {peername}")
        connected_clients.discard(writer)
        if not writer.is_closing():
            writer.close()
            await writer.wait_closed()

async def main():
    """
    Main function to start the server and background tasks.
    """
    logging.info(f"Starting plain TCP socket server on {HOST}:{PORT}")
    
    server = await asyncio.start_server(connection_handler, HOST, PORT)

    # Create and run background task
    file_watcher_task = asyncio.create_task(file_watcher())

    async with server:
        # The server runs in the background while we await the file_watcher task
        await asyncio.gather(file_watcher_task, server.serve_forever())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server shutting down.")
