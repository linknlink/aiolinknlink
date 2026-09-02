"""Probe DNA authentication header variants without sending device commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
from typing import Any

from aiolinknlink.protocol import dna


def _bound_exchange(local_ip: str, local_port: int) -> dna.PacketExchange:
    async def exchange(
        target_ip: str,
        target_port: int,
        packet: bytes,
        timeout: float,
        accept: dna.PacketAcceptor | None,
    ) -> bytes:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind((local_ip, local_port))
        try:
            await loop.sock_sendto(sock, packet, (target_ip, target_port))
            deadline = loop.time() + timeout
            while (remaining := deadline - loop.time()) > 0:
                response, remote = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 8192),
                    timeout=remaining,
                )
                if remote[0] == target_ip and (accept is None or accept(response)):
                    return response
        finally:
            sock.close()
        raise dna.DNAError(f"timeout waiting for DNA response from {target_ip}:{target_port}")

    return exchange


async def _probe(args: argparse.Namespace) -> list[dict[str, Any]]:
    exchange = _bound_exchange(args.local_ip, args.local_port) if args.local_ip else None
    framings = args.framing or ["blc", "full"]
    mac_orders = args.mac_order or ["actual", "wire"]
    payload_styles = args.payload_style or ["legacy", "generated"]
    results: list[dict[str, Any]] = []
    for mac_index, mac_text in enumerate(args.mac):
        mac = dna.mac_bytes(mac_text)
        if not mac:
            raise ValueError(f"invalid MAC address at index {mac_index}")
        for device_type in args.device_type:
            for mac_order in mac_orders:
                protocol_mac = mac if mac_order == "actual" else bytes(reversed(mac))
                for payload_style in payload_styles:
                    payload = (
                        dna.build_legacy_auth_payload(protocol_mac)
                        if payload_style == "legacy"
                        else dna.build_auth_payload(protocol_mac, device_type, host=args.host)
                    )
                    for framing in framings:
                        row: dict[str, Any] = {
                            "mac_index": mac_index,
                            "device_type": f"0x{device_type:04X}",
                            "mac_order": mac_order,
                            "payload_style": payload_style,
                            "framing": framing,
                        }
                        try:
                            response = await dna.send_encrypted(
                                args.host,
                                args.port,
                                dna.NetworkHeader(
                                    device_type=device_type,
                                    message_type=dna.MESSAGE_TYPE_AUTH,
                                    mac=protocol_mac,
                                ),
                                payload,
                                dna.INITIAL_KEY,
                                timeout=args.timeout,
                                exchange=exchange,
                                force_blc=framing == "blc",
                            )
                            dna.extract_session_key(response)
                        except dna.ShortResponseError as err:
                            row.update(
                                status="rejected",
                                response_status=f"0x{err.status:04X}",
                                response_device_type=f"0x{err.device_type:04X}",
                                response_message_type=f"0x{err.message_type:04X}",
                            )
                        except (OSError, dna.DNAError) as err:
                            row.update(status="failed", error=str(err) or type(err).__name__)
                        else:
                            row["status"] = "passed"
                        results.append(row)
                        if args.interval:
                            await asyncio.sleep(args.interval)
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--mac", action="append", required=True)
    parser.add_argument("--port", type=int, default=dna.DEFAULT_PORT)
    parser.add_argument("--local-ip")
    parser.add_argument("--local-port", type=int, default=dna.DEFAULT_PORT)
    parser.add_argument(
        "--device-type",
        action="append",
        type=lambda value: int(value, 0),
        required=True,
    )
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--mac-order", action="append", choices=("actual", "wire"))
    parser.add_argument("--payload-style", action="append", choices=("legacy", "generated"))
    parser.add_argument("--framing", action="append", choices=("blc", "full"))
    return parser.parse_args()


def main() -> None:
    """Run the authentication-only probe matrix."""
    print(json.dumps(asyncio.run(_probe(_parse_args())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
