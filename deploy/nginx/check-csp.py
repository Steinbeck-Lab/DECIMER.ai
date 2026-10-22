"""Checks that the CSP nginx sends is exactly the one web/vite.config.ts defines.

    python3 -I deploy/nginx/check-csp.py

Exit 0 and "identical" when they match, 1 and both strings when they differ. vite.config.ts is the
source of truth (`vite preview` serves it to the end-to-end tests); security-headers.conf must
follow.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def vite_policy() -> str:
    source = (ROOT / "web" / "vite.config.ts").read_text()
    block = re.search(r"CONTENT_SECURITY_POLICY\s*=\s*\[(.*?)\]\s*\.join\('; '\)", source, re.S)
    if block is None:
        sys.exit("check-csp: CONTENT_SECURITY_POLICY not found in web/vite.config.ts")
    # A string literal is either "..." or '...'; scanning left to right, a quote inside the other
    # kind of literal is part of it.
    literals = re.findall(r"\"([^\"]*)\"|'([^']*)'", block.group(1))
    return "; ".join(double or single for double, single in literals)


def nginx_policy() -> str:
    source = (ROOT / "deploy" / "nginx" / "security-headers.conf").read_text()
    header = re.search(r'add_header Content-Security-Policy "([^"]*)" always;', source)
    if header is None:
        sys.exit("check-csp: Content-Security-Policy header not found in security-headers.conf")
    return header.group(1)


def main() -> int:
    vite, nginx = vite_policy(), nginx_policy()
    if vite == nginx:
        print("identical:", nginx)
        return 0
    print("vite :", vite)
    print("nginx:", nginx)
    return 1


if __name__ == "__main__":
    sys.exit(main())
