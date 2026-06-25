"""Auto-start Partner instances on Linux boot using systemd user services."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_TEMPLATE = """[Unit]
Description=Partner AI - Instance {instance_id}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={code_dir}
ExecStart={python} -m partner --instance-id {instance_id} --workspace {working_dir}
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1

[Install]
WantedBy=default.target
"""


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_python() -> str:
    """Find the Python executable that can run Partner."""
    candidates = [
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return "/usr/bin/python3"


def install_service(instance_id: str, working_dir: str, user: bool = True) -> str:
    """Install a systemd service for the given Partner instance.
    
    Returns the service name.
    """
    python = find_python()
    service_name = f"partner-{instance_id}"

    # Determine service directory
    if user:
        # User service: ~/.config/systemd/user/
        service_dir = Path.home() / ".config" / "systemd" / "user"
    else:
        service_dir = Path("/etc/systemd/system")

    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / f"{service_name}.service"

    content = SERVICE_TEMPLATE.format(
        instance_id=instance_id,
        working_dir=working_dir,
        python=python,
        code_dir=_CODE_DIR,
    )

    service_path.write_text(content, encoding="utf-8")
    print(f"  Created {service_path}")

    # Enable and start
    scope = "--user" if user else ""
    try:
        subprocess.run(
            f"systemctl {scope} daemon-reload", shell=True, check=True,
            capture_output=True, timeout=10,
        )
        subprocess.run(
            f"systemctl {scope} enable {service_name}", shell=True, check=True,
            capture_output=True, timeout=10,
        )
        print(f"  Enabled {service_name} (will start on boot)")
    except subprocess.CalledProcessError as e:
        print(f"  Warning: systemctl failed: {e.stderr.decode() if e.stderr else e}")

    return service_name


def install_all(workspace: str | None = None) -> list[str]:
    """Install auto-start services for all enabled instances in the workspace."""
    if workspace is None:
        from partner.instance_root import resolve_partner_root
        workspace = str(resolve_partner_root())

    from partner.config import load_global_config
    config = load_global_config(workspace)
    instances = config.get("instances", {})

    services = []
    for inst_id, info in instances.items():
        if not info.get("enabled", True):
            continue
        working_dir = info.get("working_dir", "")
        if not working_dir or not os.path.isdir(working_dir):
            # Resolve from workspace/instances/<id>
            working_dir = os.path.join(workspace, "instances", inst_id)
            if not os.path.isdir(working_dir):
                print(f"  Skipping {inst_id}: working_dir not found")
                continue
        print(f"Installing auto-start for instance {inst_id}...")
        srv = install_service(inst_id, working_dir)
        services.append(srv)

    return services


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Install Partner auto-start services")
    parser.add_argument("--workspace", "-w", default=None, help="Workspace path")
    parser.add_argument("--instance", "-i", default=None, help="Specific instance ID (optional)")
    args = parser.parse_args()

    if args.instance:
        # Find the working dir for this instance
        if args.workspace:
            ws = args.workspace
        else:
            from partner.instance_root import resolve_partner_root
            ws = str(resolve_partner_root())
        from partner.config import load_global_config
        config = load_global_config(ws)
        inst_info = config.get("instances", {}).get(args.instance, {})
        if not inst_info:
            print(f"Instance {args.instance} not found in workspace")
            sys.exit(1)
        working_dir = inst_info.get("working_dir", os.path.join(ws, "instances", args.instance))
        srv = install_service(args.instance, working_dir)
        print(f"\nDone. Service: {srv}")
        print(f"Run: systemctl --user start {srv}")
    else:
        services = install_all(args.workspace)
        print(f"\nDone. {len(services)} service(s) installed:")
        for s in services:
            print(f"  systemctl --user start {s}")
