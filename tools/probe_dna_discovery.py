"""Probe DNA discovery from a selected local UDP port."""

from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import UTC, datetime

from aiolinknlink.protocol import dna


def main() -> None:
    """Send both discovery packet variants and print parsed responses."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--local-port", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()

    responses: list[dict[str, object]] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", args.local_port))
        bound_port = int(sock.getsockname()[1])
        now = datetime.now(UTC)
        for builder in (dna.build_discovery_packet, dna.build_short_discovery_packet):
            sock.sendto(builder(args.local_ip, bound_port, now), (args.host, dna.DEFAULT_PORT))
        deadline = time.monotonic() + args.timeout
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                data, remote = sock.recvfrom(4096)
            except TimeoutError:
                break
            try:
                device = dna.parse_discovery_device_response(data, remote[0], remote[1])
            except dna.DNAError:
                continue
            responses.append(
                {
                    "ip": device.ip,
                    "port": device.port,
                    "type_id": f"0x{device.device_type:04X}",
                    "message_type": f"0x{device.message_type:04X}",
                    "mac": device.mac,
                    "name": device.name,
                    "status_flags": (f"0x{device.status_flags:02X}" if device.status_flags is not None else None),
                    "is_new": device.is_new,
                    "is_locked": device.is_locked,
                }
            )
    print(json.dumps({"bound_port": bound_port, "responses": responses}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
