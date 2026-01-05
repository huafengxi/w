#!/usr/bin/env python3
import requests
import os
import random
import sys

# Configuration
SERVER_BASE_URL = "http://127.0.0.1:8080/nvr"
TEST_FILENAME = "random_test_file.bin"
FILE_SIZE_MB = 10 # 1 MB test file
FILE_SIZE_BYTES = FILE_SIZE_MB * 1024 * 1024

def generate_random_file(filename, size_bytes):
    """Generates a file with random bytes."""
    print(f"Generating random file: {filename} of size {size_bytes / (1024 * 1024):.2f} MB...")
    random_bytes = os.urandom(size_bytes)
    with open(filename, 'wb') as f:
        f.write(random_bytes)
    print("File generated.")
    return random_bytes

def write_file_to_server(filename, content):
    """Writes the file content to the server."""
    print(f"Writing {filename} to server...")
    write_url = f"{SERVER_BASE_URL}/{filename}?v=write&post=store_content"
    try:
        response = requests.post(write_url, data=content)
        response.raise_for_status() # Raise an exception for HTTP errors
        print(f"File written successfully. Server response: {response.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error writing file to server: {e}", file=sys.stderr)
        return False

def read_file_from_server(filename, offset=None, length=None):
    """Reads file content (or a part of it) from the server."""
    read_url = f"{SERVER_BASE_URL}/{filename}"
    headers = {}

    if offset is not None and length is not None:
        end = offset + length - 1 # Range header is inclusive
        headers['Range'] = f"bytes={offset}-{end}"
        print(f"Reading part of {filename} from server (Range: bytes={offset}-{end})...")
    else:
        print(f"Reading entire {filename} from server...")

    try:
        response = requests.get(read_url, headers=headers)
        response.raise_for_status()
        print(f"Read successful. Status code: {response.status_code}")
        return response.content
    except requests.exceptions.RequestException as e:
        print(f"Error reading file from server: {e}", file=sys.stderr)
        return None

def main():
    original_content = None
    try:
        # 1. Generate a random file
        original_content = generate_random_file(TEST_FILENAME, FILE_SIZE_BYTES)
        if not original_content:
            return

        # 2. Write the file to the server
        if not write_file_to_server(TEST_FILENAME, original_content):
            return

        # 3. Read the entire file from the server and verify
        print("\nVerifying entire file read...")
        server_full_content = read_file_from_server(TEST_FILENAME)
        if server_full_content is None:
            print("Full file read failed.")
            return

        if server_full_content == original_content:
            print("Full file read verification successful!")
        else:
            print("Full file read verification FAILED: Content mismatch!", file=sys.stderr)
            return

        # 4. Test partial reads
        print("\nTesting partial reads...")
        num_partial_tests = 10
        all_partial_tests_passed = True
        for i in range(num_partial_tests):
            offset = random.randint(0, FILE_SIZE_BYTES - 1)
            length = random.randint(1, FILE_SIZE_BYTES - offset) # Ensure length doesn't exceed file end

            expected_partial = original_content[offset : offset + length]
            server_partial_content = read_file_from_server(TEST_FILENAME, offset, length)

            if server_partial_content is None:
                print(f"Partial read {i+1} FAILED: Could not read from server.", file=sys.stderr)
                all_partial_tests_passed = False
                continue

            if server_partial_content == expected_partial:
                print(f"Partial read {i+1} (offset={offset}, length={length}) successful.")
            else:
                print(f"Partial read {i+1} (offset={offset}, length={length}) FAILED: Content mismatch!", file=sys.stderr)
                all_partial_tests_passed = False

        if all_partial_tests_passed:
            print("\nAll partial read tests passed!")
        else:
            print("\nSome partial read tests FAILED!", file=sys.stderr)

    finally:
        # Clean up local test file
        if os.path.exists(TEST_FILENAME):
            os.remove(TEST_FILENAME)
            print(f"\nCleaned up local file: {TEST_FILENAME}")

if __name__ == "__main__":
    main()
