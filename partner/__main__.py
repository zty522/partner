"""Partner CLI and pure-Event instance entrypoint."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


KNOWN_CLI_COMMANDS = frozenset({
    "setup", "status", "help", "doctor", "start", "stop", "restart", "bot",
    "update", "instance", "showcase", "server", "ollama", "onboard", "gateway",
    "world-model", "wm", "tui", "queue", "config", "desktop",
})


def _looks_like_instance_launch(argv: list[str]) -> bool:
    return bool(not (argv and argv[0] in KNOWN_CLI_COMMANDS) and any(
        item == "--instance-id" or item.startswith("--instance-id=")
        or item == "--workspace" or item.startswith("--workspace=") for item in argv))


def validate_pdf(file_path: str) -> tuple[bool, str]:
    try:
        with open(file_path, "rb") as handle:
            if handle.read(5) != b"%PDF-":
                return False, "Invalid PDF magic number"
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size < 1024:
                return False, "PDF is too small to be a user report"
            handle.seek(max(0, size - 2048))
            if b"%%EOF" not in handle.read():
                return False, "Missing PDF EOF marker"
        return True, "Valid PDF structure"
    except Exception as exc:
        return False, f"Error validating PDF: {exc}"


def correct_extension(file_data: bytes, filename: str) -> str:
    stem, extension = os.path.splitext(filename)
    if file_data[:8] == b"\x89PNG\r\n\x1a\n":
        return filename if extension.lower() == ".png" else stem + ".png"
    if file_data[:3] == b"\xff\xd8\xff":
        return filename if extension.lower() in {".jpg", ".jpeg"} else stem + ".jpg"
    if extension.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
        try:
            text = file_data[:512].decode("utf-8")
            if text.lstrip().startswith(("#", "<", "{")) or "\n" in text:
                return stem + ".md"
        except UnicodeDecodeError:
            pass
    return filename


def _run_instance_mode(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m partner")
    parser.add_argument("--instance-id", default=os.environ.get("PARTNER_INSTANCE_ID", "default"))
    parser.add_argument("--workspace", default=os.environ.get("PARTNER_WORKSPACE", ""))
    args = parser.parse_args(argv)
    from partner.monitoring.instance_root import resolve_instance_workspace
    workspace = Path(args.workspace or resolve_instance_workspace(args.instance_id)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    from partner.monitoring.run_control import is_instance_paused
    if is_instance_paused(str(workspace), args.instance_id):
        print(f"Partner instance '{args.instance_id}' is persistently paused; startup skipped.", flush=True)
        return
    from partner.monitoring.instance_lock import InstanceAlreadyRunning, acquire_instance_lock
    try:
        lock = acquire_instance_lock(str(workspace), args.instance_id)
    except InstanceAlreadyRunning as exc:
        print(f"Partner instance '{args.instance_id}' is already running: {exc}", flush=True)
        return
    (workspace / "instance.pid").write_text(str(os.getpid()), encoding="utf-8")
    os.environ["PARTNER_INSTANCE_ID"] = args.instance_id
    os.environ["PARTNER_WORKSPACE"] = str(workspace)
    from partner.runtime.instance_host import run_instance_host
    try:
        run_instance_host(str(workspace), args.instance_id)
    finally:
        try:
            (workspace / "instance.pid").unlink()
        except OSError:
            pass
        del lock


def main() -> None:
    argv = sys.argv[1:]
    if _looks_like_instance_launch(argv):
        _run_instance_mode(argv)
        return
    from partner.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
