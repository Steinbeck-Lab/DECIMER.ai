# https://realpython.com/python-sockets/#multi-connection-client-and-server
import sys
import os
import socket
import selectors
import types
from DECIMER import predict_SMILES

sel = selectors.DefaultSelector()


def resolve_image_path(path: str):
    """
    Resolve the actual path to the image file, handling both PHP 7.4 and 8.2 structures
    """
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

    for test_path in possible_paths:
        if os.path.exists(test_path):
            print(f"Found file at: {test_path}")
            return test_path

    print(f"File not found. Tried paths: {possible_paths}")
    return None


def accept_wrapper(sock):
    """Accept connection from client"""
    conn, addr = sock.accept()
    print(f"Accepted connection from {addr}")
    data = types.SimpleNamespace(addr=addr, inb=b"", outb=b"")
    events = selectors.EVENT_READ | selectors.EVENT_WRITE
    sel.register(conn, events, data=data)


def service_connection(key, mask):
    """Handle connection and process OCSR request"""
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
            # Process the received path
            input_path = data.outb.decode("utf-8")
            print(f"Processing OCSR for path: {input_path}")

            # Resolve the actual file path
            actual_path = resolve_image_path(input_path)

            if actual_path:
                try:
                    # Run DECIMER OCSR
                    SMILES = predict_SMILES(actual_path)
                    print(f"OCSR result: {SMILES}")
                except Exception as e:
                    print(f"OCSR failed: {e}")
                    SMILES = ""
            else:
                print("File not found for OCSR")
                SMILES = ""

            # Send response
            if len(SMILES.split(".")) > 4 or len(SMILES.split(".")[0]) < sum(
                len(part) for part in SMILES.split(".")[1:]
            ):
                processed_info = SMILES.split(".")[0].encode("utf-8")
            else:
                processed_info = SMILES.encode("utf-8")
            print(f"Sending OCSR response: {SMILES}")
            sock.send(processed_info)
            data.outb = b""


def run_server(port: int):
    """Start the OCSR server"""
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
    """Start DECIMER OCSR server"""
    print(f"Setting up DECIMER OCSR Server on port {sys.argv[1]}")
    print(f"Current working directory: {os.getcwd()}")
    run_server(int(sys.argv[1]))


if __name__ == "__main__":
    main()
