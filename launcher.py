"""grokweb launcher.

Ensures a grok *leader* is running (so a terminal TUI and the web share one live
session), then serves the web UI and prints LAN URLs plus a scannable QR code so
you can open it on your phone.

Usage:
    python launcher.py [--port 8787] [--no-leader] [--tunnel]

``--tunnel`` exposes the UI over the internet via a Cloudflare quick tunnel
(auto-downloads cloudflared if needed). Only the token-bearing QR link works;
anyone else hitting the public URL gets 403.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def leader_running() -> bool:
    try:
        out = subprocess.run(
            [config.GROK_EXE, "leader", "list"],
            capture_output=True, text=True, timeout=20,
        )
        return "No leader candidates" not in (out.stdout + out.stderr)
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_leader() -> None:
    if leader_running():
        print("[grokweb] leader already running — terminal and web will mirror.")
        return
    print("[grokweb] starting shared leader…")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    try:
        subprocess.Popen(
            [config.GROK_EXE, "agent", "leader", "--no-exit-on-disconnect", "--relay-on-demand"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
        time.sleep(2.0)
    except OSError as exc:
        print(f"[grokweb] could not auto-start leader ({exc}); running standalone.")


def _enable_utf8_console() -> None:
    """Make the console render Unicode block characters (the QR code).

    Windows' Traditional-Chinese console defaults to the cp950/Big5 code page,
    which can't encode the block glyphs qrcode uses -> UnicodeEncodeError. Switch
    both Python's stream encoding and the console output code page to UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def _print_qr(url: str) -> None:
    try:
        import qrcode  # type: ignore
        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        # Half-block rendering: two module-rows per text line -> square, scannable.
        # On a dark terminal, dark QR modules are drawn as bright blocks.
        for y in range(0, len(matrix), 2):
            line = []
            for x in range(len(matrix[0])):
                top = matrix[y][x]
                bot = matrix[y + 1][x] if y + 1 < len(matrix) else False
                if top and bot:
                    line.append("█")
                elif top:
                    line.append("▀")
                elif bot:
                    line.append("▄")
                else:
                    line.append(" ")
            print("".join(line))
    except Exception as exc:
        print(f"  (QR 顯示失敗：{exc}） — 請直接用上面的網址")


def print_banner(port: int, public_url: str | None) -> None:
    ip = lan_ip()
    key = config.ACCESS_TOKEN
    lan_url = f"http://{ip}:{port}/?key={key}"
    print("\n" + "=" * 56)
    print("  GrokWeb 已啟動")
    print(f"  本機:  http://127.0.0.1:{port}/   (免 token)")
    print(f"  區網:  {lan_url}")
    if public_url:
        share = f"{public_url}/?key={key}"
        print(f"  外網:  {share}")
        print("=" * 56)
        print("  掃描以下 QR 從任何網路連入（沒有此連結的人會被 403 擋下）：")
        _print_qr(share)
    else:
        print("=" * 56)
        print("  掃描以下 QR 從手機連入（需同一 Wi-Fi）：")
        _print_qr(lan_url)
        print("\n  想從外網（非同一 Wi-Fi）連入？改用：python launcher.py --tunnel")
    print()


# ------------------------------------------------------------------ tunnel
def _cloudflared_path() -> str:
    import shutil
    env = os.environ.get("CLOUDFLARED_EXE")
    if env and Path(env).exists():
        return env
    found = shutil.which("cloudflared")
    if found:
        return found
    local = ROOT / "bin" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    return str(local)


def ensure_cloudflared() -> str:
    path = _cloudflared_path()
    if Path(path).exists() or (os.name != "nt" and __import__("shutil").which("cloudflared")):
        return path
    if os.name == "nt":
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    elif sys.platform == "darwin":
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz"
    else:
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    print(f"[grokweb] 下載 cloudflared… ({url})")
    urllib.request.urlretrieve(url, path)
    if os.name != "nt":
        os.chmod(path, 0o755)
    print(f"[grokweb] cloudflared 已存到 {path}")
    return path


def start_tunnel(port: int) -> tuple[str | None, subprocess.Popen | None]:
    exe = ensure_cloudflared()
    print("[grokweb] 啟動 Cloudflare quick tunnel…")
    proc = subprocess.Popen(
        [exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    url_box: dict[str, str] = {}
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def reader() -> None:
        assert proc.stdout
        for line in proc.stdout:
            if "url" not in url_box:
                m = pattern.search(line)
                if m:
                    url_box["url"] = m.group(0)

    threading.Thread(target=reader, daemon=True).start()
    for _ in range(120):  # wait up to ~30s
        if "url" in url_box:
            return url_box["url"], proc
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    print("[grokweb] 無法建立 tunnel（cloudflared 未回傳網址）。改用區網。")
    return None, proc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--no-leader", action="store_true", help="不啟動/連接 leader（網頁獨立運作）")
    parser.add_argument("--tunnel", action="store_true", help="用 Cloudflare tunnel 對外開放（帶 token 的連結才可進）")
    args = parser.parse_args()

    _enable_utf8_console()

    if args.no_leader:
        os.environ["GROKWEB_USE_LEADER"] = "0"
        config.USE_LEADER = False
    else:
        ensure_leader()

    tunnel_proc = None
    public_url = None
    if args.tunnel:
        public_url, tunnel_proc = start_tunnel(args.port)

    # grok agent runs as a child process; the Proactor loop supports subprocess pipes on Windows.
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    print_banner(args.port, public_url)

    import uvicorn
    try:
        uvicorn.run("backend.main:app", host=args.host, port=args.port, loop="asyncio", log_level="info")
    finally:
        if tunnel_proc and tunnel_proc.poll() is None:
            tunnel_proc.terminate()


if __name__ == "__main__":
    main()
