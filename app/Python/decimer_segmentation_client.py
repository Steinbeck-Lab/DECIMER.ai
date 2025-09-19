import sys
import random
import json
from itertools import cycle
from multiprocessing import Pool
import socket


def send_and_receive(input_path: str, port: int):
    HOST = "supervisor"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(30)
            s.connect((HOST, port))
            s.sendall(input_path.encode("utf-8"))
            data = s.recv(32768)
            return data.decode("utf-8")
    except Exception as e:
        return json.dumps([])


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

    except (IndexError, json.JSONDecodeError):
        print(json.dumps([]))
        return

    num_ports = 6
    ports = list(range(23456, 23456 + num_ports))
    random.shuffle(ports)
    ports = cycle(ports)

    starmap_tuples = [(path, next(ports)) for path in paths]

    try:
        with Pool(len(paths)) as p:
            json_array_path_strings = p.starmap(send_and_receive, starmap_tuples)

        all_paths = []
        for path_string in json_array_path_strings:
            if path_string and path_string.strip():
                try:
                    parsed = json.loads(path_string)
                    if isinstance(parsed, list):
                        all_paths.extend(parsed)
                    else:
                        all_paths.append(parsed)
                except json.JSONDecodeError:
                    continue

        print(json.dumps(all_paths))

    except Exception:
        print(json.dumps([]))


if __name__ == "__main__":
    main()
