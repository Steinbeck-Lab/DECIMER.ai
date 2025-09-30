import sys
import json
import socket
from multiprocessing import Pool
from itertools import cycle
import random


def test_port(port):
    """Test if a port is accepting connections."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(("supervisor", port))
            return port
    except:
        return None


def get_available_ports(port_range):
    """Discover which ports are actually available."""
    available = []
    for port in port_range:
        if test_port(port):
            available.append(port)
    return available


def send_and_receive(path, port):
    """Send image path to OCSR server and receive SMILES."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("supervisor", port))
            s.sendall(path.encode("utf-8"))
            data = s.recv(32768)
            return data.decode("utf-8")
    except Exception as e:
        print(f"Error on port {port}: {e}", file=sys.stderr)
        return ""


def main():
    try:
        if len(sys.argv) < 2:
            print(json.dumps([]))
            return

        json_input = sys.argv[1]
        paths = json.loads(json_input)

        if not isinstance(paths, list) or not paths:
            print(json.dumps([]))
            return

    except (json.JSONDecodeError, IndexError) as e:
        print(f"Error parsing input: {e}", file=sys.stderr)
        print(json.dumps([]))
        return

    # Auto-discover available ports
    port_range = range(65432, 65435)  # 65432-65434
    available_ports = get_available_ports(port_range)

    if not available_ports:
        print(json.dumps([]))
        return

    # Use all available ports with load balancing
    random.shuffle(available_ports)
    ports = cycle(available_ports)

    # Create work tuples
    starmap_tuples = [(path, next(ports)) for path in paths]

    # Process in parallel if multiple ports, sequential if only one
    if len(available_ports) > 1:
        with Pool(len(paths)) as p:
            SMILES = p.starmap(send_and_receive, starmap_tuples)
    else:
        SMILES = [send_and_receive(path, available_ports[0]) for path in paths]

    print(json.dumps(SMILES))


if __name__ == "__main__":
    main()
