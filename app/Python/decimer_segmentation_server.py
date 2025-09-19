# https://realpython.com/python-sockets/#multi-connection-client-and-server
import sys
import os
import socket
import selectors
import types
import json
from decimer_segmentation import segment_chemical_structures_from_file
import cv2

sel = selectors.DefaultSelector()


def run_decimer_segmentation(path: str):
    """
    Process image with DECIMER segmentation, handling different path formats
    and ensuring compatibility with both PHP 7.4 and 8.2 file structures.
    """
    try:
        print(f"Processing path: {path}")
        print(f"Current working directory: {os.getcwd()}")

        # Comprehensive path resolution for both PHP versions
        base_filename = os.path.basename(path)
        possible_paths = [
            path,  # Original path as provided
            # PHP 8.2 paths
            os.path.join("./storage/app/public/media/", base_filename),
            os.path.join("/var/www/app/storage/app/public/media/", base_filename),
            os.path.join("./public/storage/media/", base_filename),
            # PHP 7.4 compatibility paths
            os.path.join("./storage/media/", base_filename),
            os.path.join("/var/www/app/storage/media/", base_filename),
            # Additional fallback paths
            os.path.join("./storage/app/public/media/", path),
            os.path.join("/var/www/app/", path),
        ]

        actual_path = None
        for test_path in possible_paths:
            if os.path.exists(test_path):
                actual_path = test_path
                print(f"Found file at: {actual_path}")
                break

        if not actual_path:
            print(f"File not found. Tried paths: {possible_paths}")
            return []

        # Run segmentation
        image_name = os.path.basename(actual_path)
        segments = segment_chemical_structures_from_file(actual_path)

        if not segments:
            print("No segments found")
            return []

        segment_paths = []

        for segment_index in range(len(segments)):
            filename = f"{image_name[:-4]}_{segment_index}.png"

            # Save to PHP 8.2 location but return PHP 7.4 compatible path
            segment_path = os.path.join("./storage/app/public/media/", filename)

            # Ensure directory exists
            os.makedirs(os.path.dirname(segment_path), exist_ok=True)

            # Write the segmented image
            cv2.imwrite(segment_path, segments[segment_index])

            # Return path in PHP 7.4 compatible format for client compatibility
            return_path = f"../storage/media/{filename}"
            segment_paths.append(return_path)
            print(f"Saved segment: {segment_path} -> returning: {return_path}")

        return segment_paths

    except Exception as e:
        print(f"Error in run_decimer_segmentation: {e}")
        import traceback

        traceback.print_exc()
        return []


def accept_wrapper(sock):
    """Accept connection from client"""
    conn, addr = sock.accept()
    print(f"Accepted connection from {addr}")
    data = types.SimpleNamespace(addr=addr, inb=b"", outb=b"")
    events = selectors.EVENT_READ | selectors.EVENT_WRITE
    sel.register(conn, events, data=data)


def service_connection(key, mask):
    """Handle connection and process segmentation request"""
    sock = key.fileobj
    data = key.data
    if mask & selectors.EVENT_READ:
        recv_data = sock.recv(32768)
        if recv_data:
            print(f"Received_data: {recv_data}")
            data.outb += recv_data
        else:
            print(f"Closing connection to {data.addr}")
            sel.unregister(sock)
            sock.close()
    if mask & selectors.EVENT_WRITE:
        if data.outb:
            input_path = data.outb.decode("utf-8")
            print(f"Processing path: {input_path}")

            # Run DECIMER Segmentation
            segment_paths = run_decimer_segmentation(input_path)

            # Send response
            processed_info = json.dumps(segment_paths).encode("utf-8")
            print(f"Sending response: {processed_info.decode()}")
            sock.send(processed_info)
            data.outb = b""


def run_server(port: int):
    """Start the segmentation server"""
    host = "0.0.0.0"
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        lsock.bind((host, port))
        lsock.listen()
        print(f"Listening on {(host, port)}")
        sel.register(lsock, selectors.EVENT_READ, data=None)

        while True:
            try:
                events = sel.select(timeout=None)
                for key, mask in events:
                    if key.data is None:
                        accept_wrapper(key.fileobj)
                    else:
                        service_connection(key, mask)
            except KeyboardInterrupt:
                print("Caught keyboard interrupt, exiting")
                break
    except OSError as e:
        print(f"Error binding to port {port}: {e}")
        raise
    finally:
        sel.close()
        lsock.close()


def main():
    """Start DECIMER Segmentation server"""
    print(f"Setting up DECIMER Segmentation Server on port {sys.argv[1]}")
    print(f"Current working directory: {os.getcwd()}")
    run_server(int(sys.argv[1]))


if __name__ == "__main__":
    main()
