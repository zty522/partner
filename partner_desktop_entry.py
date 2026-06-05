"""Windows desktop entry.

The frozen Partner.exe is also used by the GUI to run local Partner commands.
When PyInstaller is installed, sys.executable points at Partner.exe rather than
python.exe, so this entry must dispatch CLI/runtime arguments explicitly.
"""

from __future__ import annotations

import sys


for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


CLI_COMMANDS = {
    "setup",
    "status",
    "start",
    "stop",
    "restart",
    "bot",
    "help",
    "doctor",
    "instance",
    "showcase",
    "ollama",
    "update",
}


def _looks_like_instance_launch(argv: list[str]) -> bool:
    return any(
        arg == "--instance-id"
        or arg.startswith("--instance-id=")
        or arg == "--workspace"
        or arg.startswith("--workspace=")
        for arg in argv
    )


def main() -> int:
    argv = sys.argv[1:]

    if argv[:2] == ["-m", "partner.cli"]:
        from partner.cli import main as cli_main

        sys.argv = ["partner", *argv[2:]]
        return int(cli_main() or 0)

    if argv[:2] == ["-m", "partner"]:
        from partner.__main__ import main as partner_main

        sys.argv = ["python -m partner", *argv[2:]]
        return int(partner_main() or 0)

    if argv and argv[0] in CLI_COMMANDS:
        from partner.cli import main as cli_main

        sys.argv = ["partner", *argv]
        return int(cli_main() or 0)

    if _looks_like_instance_launch(argv):
        from partner.__main__ import main as partner_main

        sys.argv = ["python -m partner", *argv]
        return int(partner_main() or 0)

    from partner.gui_qt import launch

    return int(launch() or 0)

if __name__ == '__main__':
    raise SystemExit(main())
