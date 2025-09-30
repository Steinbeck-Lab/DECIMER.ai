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
    and ensuring compatibility with both PHP versions.
    """
    try:
        print(f"Processing path: {path}")
        print(f"Current working directory: {os.getcwd()}")

        # Comprehensive path resolution
        base_filename = os.path.basename(path)
        possible_paths = [
            path,
            os.path.join("./storage/app/public/media/", base_filename),
            os.path.join("/var/www/app/storage/app/public/media/", base_filename),
            os.path.join("./public/storage/media/", base_filename),
            os.path.join("./storage/media/", base_filename),
            os.path.join("/var/www/app/storage/media/", base_filename),
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

        # Check image size
        check_img = cv2.imread(actual_path)
        if check_img is None:
            print(f"Failed to read image: {actual_path}")
            return []

        height, width = check_img.shape[:2]
        print(f"Image dimensions: {width}x{height}")

        image_name = os.path.basename(actual_path)
        segment_paths = []
        save_dir = "/var/www/app/storage/app/public/media/"

        # Ensure directory exists
        os.makedirs(save_dir, exist_ok=True)

        # If image is smaller than 1500x1500, skip segmentation
        if width < 1500 or height < 1500:
            print(f"Image too small ({width}x{height}), skipping segmentation")
            filename = f"{image_name[:-4]}_0.png"
            segment_path = os.path.join(save_dir, filename)
            cv2.imwrite(segment_path, check_img)

            # Return web-accessible path
            return_path = f"storage/media/{filename}"
            segment_paths.append(return_path)
            print(f"Saved original image: {segment_path} -> returning: {return_path}")
            return segment_paths

        # Run segmentation for larger images
        segments = segment_chemical_structures_from_file(actual_path)

        if not segments:
            print("No segments found")
            return []

        for segment_index in range(len(segments)):
            filename = f"{image_name[:-4]}_{segment_index}.png"

            # Save to absolute path
            segment_path = os.path.join(save_dir, filename)
            cv2.imwrite(segment_path, segments[segment_index])

            # Return web-accessible path
            return_path = f"storage/media/{filename}"
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
