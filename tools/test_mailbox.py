"""A test mail box on this PC: schedule e-mails are saved as .eml files instead of being sent. No packages needed.

    python tools/test_mailbox.py                        listens on 127.0.0.1:2525, saves into data/test-mail
    python tools/test_mailbox.py --port 2526 --dir C:/mail

Point the service at it (tools/harden_env.py --test-mailbox does this):
    SMTP_HOST=127.0.0.1  SMTP_PORT=2525  SMTP_SECURITY=none  SMTP_FROM=reports@localhost
Nothing leaves this PC. Open a saved .eml file with Outlook / Thunderbird, or read it as text.
Only for testing: use a real mail server for real schedules.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def make_handler(folder: Path):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"220 test mailbox\r\n")
        lines: list[bytes] = []
        in_data = False
        while line := await reader.readline():
            if in_data:
                if line in (b".\r\n", b".\n"):
                    in_data = False
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / f"{datetime.now():%Y%m%d-%H%M%S-%f}.eml"
                    path.write_bytes(b"".join(lines))
                    print(f"saved {path.name} ({sum(map(len, lines))} bytes)", flush=True)
                    lines = []
                    writer.write(b"250 OK\r\n")
                else:
                    lines.append(line[1:] if line.startswith(b"..") else line)
                continue
            command = line[:4].upper()
            if command == b"DATA":
                in_data = True
                writer.write(b"354 end with a dot on a line of its own\r\n")
            elif command == b"QUIT":
                writer.write(b"221 bye\r\n")
                await writer.drain()
                break
            elif command in (b"EHLO", b"HELO"):
                writer.write(b"250-test mailbox\r\n250 8BITMIME\r\n" if command == b"EHLO" else b"250 test mailbox\r\n")
            else:
                writer.write(b"250 OK\r\n")
            await writer.drain()
        writer.close()
    return handle


async def serve(host: str, port: int, folder: Path) -> None:
    server = await asyncio.start_server(make_handler(folder), host, port)
    print(f"Test mail box on {host}:{port}, saving into {folder} (Ctrl+C stops it)", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="A test SMTP mail box that saves mail as files")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2525)
    parser.add_argument("--dir", default=str(ROOT / "data" / "test-mail"))
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.host, args.port, Path(args.dir)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
