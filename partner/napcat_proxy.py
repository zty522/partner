"""NapCat WebSocket Proxy - 桥接 WSL2 到 Windows NapCat WebSocket.

WSL2 有独立的网络栈，127.0.0.1:3001 指 WSL 内部而非 Windows。
本模块自动检测 Windows 主机 IP，创建本地端口转发，让 WSL 中的
Partner QQ Bridge 能连接到 Windows 上运行的 NapCat OneBot WebSocket。

架构:
    QQ User → NapCat (Windows, 127.0.0.1:3001)
                → Windows 主机防火墙 (172.26.x.1)
                    → socat 转发 (WSL localhost:13001)
                        → Partner QQ Bridge (WSL)

使用方式:
    from partner.napcat_proxy import NapCatProxy

    proxy = NapCatProxy(remote_port=3001, local_port=13001)
    proxy.start()  # 启动转发（后台线程）
    # ... 用 ws://localhost:13001 连接 ...
    proxy.stop()   # 停止转发

自动检测:
    - 读取 /etc/resolv.conf 获取 nameserver (Windows 主机)
    - 回退: 尝试 172.26.176.1 (WSL 默认网关)
    - 回退: 尝试 host.docker.internal (Docker 默认)
"""

import os
import re
import sys
import time
import socket
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


# ── Windows 主机 IP 检测 ──────────────────────────────────────────

# ── 内部: 已知非 Windows 主机 IP 过滤器 ─────────────────────
_KNOWN_NON_WINDOWS_IPS = {
    "100.100.100.100",   # Tailscale DNS
    "1.1.1.1",            # Cloudflare DNS
    "8.8.8.8",            # Google DNS
    "208.67.222.222",     # OpenDNS
    "9.9.9.9",            # Quad9
}


def _is_windows_host_candidate(ip: str) -> bool:
    """判断 IP 是否可能是 Windows 主机。

    过滤规则:
    - 排除已知的非 Windows IP（DNS 等）
    - 必须是私有 IPv4 地址（10.x.x.x, 172.16-31.x.x, 192.168.x.x）
    """
    if ip in _KNOWN_NON_WINDOWS_IPS:
        return False
    if ":" in ip:
        return False
    # 检查私有 IP 范围
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    first = int(parts[0])
    second = int(parts[1])
    if first == 10:
        return True
    if first == 172 and 16 <= second <= 31:
        return True
    if first == 192 and second == 168:
        return True
    return False


def detect_windows_host_ip() -> str:
    """检测 Windows 主机 IP 地址。

    检测顺序:
    1. **默认网关（`ip route show default`）** ← 最可靠，优先
    2. /etc/resolv.conf 中的 nameserver（WSL 标准方法，但可能被 Tailscale 覆盖）
    3. host.docker.internal（Docker 默认）
    4. 从 WSL eth0 IP 推导网关 (172.x.x.1)

    Returns:
        Windows 主机 IP 字符串，未找到则返回空字符串。
    """
    candidates = []

    # 方法 1: 默认网关（WSL 中通常是 Windows 主机，最可靠）
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r"default via (\S+)", result.stdout)
        if m:
            ip = m.group(1)
            if _is_windows_host_candidate(ip) and ip not in candidates:
                candidates.append(ip)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 方法 2: /etc/resolv.conf 中的 nameserver
    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                m = re.match(r"^nameserver\s+(\S+)", line)
                if m:
                    ip = m.group(1)
                    if _is_windows_host_candidate(ip) and ip not in candidates:
                        candidates.append(ip)
    except (FileNotFoundError, PermissionError):
        pass

    # 方法 3: host.docker.internal（Docker 默认）
    try:
        host_ip = socket.gethostbyname("host.docker.internal")
        if _is_windows_host_candidate(host_ip) and host_ip not in candidates:
            candidates.append(host_ip)
    except (socket.gaierror, OSError):
        pass

    # 方法 4: 尝试从 WSL 默认网关推导 (172.x.x.1)
    try:
        result = subprocess.run(
            ["ip", "addr", "show", "eth0"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r"inet (\d+\.\d+\.\d+)\.", result.stdout)
        if m:
            gateway_ip = f"{m.group(1)}.1"
            if _is_windows_host_candidate(gateway_ip) and gateway_ip not in candidates:
                candidates.append(gateway_ip)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 返回第一个候选
    for ip in candidates:
        if _is_host_reachable(ip, port=3001, timeout=2):
            logger.info(f"✅ Windows 主机 IP 已确认: {ip}")
            return ip

    # 没有任何 IP 可达，返回第一个候选
    if candidates:
        logger.warning(f"⚠️  无法确认 Windows 主机可达性，使用: {candidates[0]}")
        return candidates[0]

    logger.error("❌ 无法检测 Windows 主机 IP")
    return ""


def _is_host_reachable(host: str, port: int = 3001, timeout: int = 2) -> bool:
    """检查主机端口是否可达。"""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def check_napcat_alive(host: str = "localhost", port: int = 3001) -> bool:
    """检查 NapCat OneBot WebSocket 是否在线。

    先检查本地端口，再尝试 Windows 主机。
    """
    if _is_host_reachable(host, port, timeout=1):
        return True
    # 本地不通，尝试 Windows 主机
    win_ip = detect_windows_host_ip()
    if win_ip and _is_host_reachable(win_ip, port, timeout=2):
        return True
    return False


# ── Proxy ─────────────────────────────────────────────────────────

@dataclass
class NapCatProxyConfig:
    """NapCat 代理配置。"""
    windows_host: str = ""           # Windows 主机 IP（自动检测）
    remote_port: int = 3001          # NapCat WebSocket 端口
    local_port: int = 13001          # 本地转发端口
    auto_detect: bool = True         # 自动检测 Windows 主机 IP
    retry_interval: float = 5.0      # 重连间隔（秒）
    max_retries: int = 3             # 最大重试次数


class NapCatProxy:
    """WSL↔Windows NapCat WebSocket 端口转发代理。

    在本地 (localhost:local_port) 监听，转发到
    Windows 主机 (windows_host:remote_port)。

    Windows 侧 NapCat 配置需要将 host 改为 0.0.0.0
    或添加 Windows 防火墙入站规则允许 WSL 访问。
    """

    def __init__(self, config: NapCatProxyConfig = None, **kwargs):
        self.config = config or NapCatProxyConfig(**kwargs)
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._retry_count = 0

    # ── 公共 API ──────────────────────────────────────────────

    def start(self, blocking: bool = False):
        """启动转发代理。

        Args:
            blocking: True 则阻塞等待进程退出；False 则在后台线程运行。
        """
        if self._running:
            logger.warning("代理已在运行中")
            return

        # 自动检测 Windows 主机
        if self.config.auto_detect and not self.config.windows_host:
            self.config.windows_host = detect_windows_host_ip()
            if not self.config.windows_host:
                logger.error("❌ 无法自动检测 Windows 主机 IP，请手动设置")
                return

        self._running = True
        host = self.config.windows_host
        remote = self.config.remote_port
        local = self.config.local_port

        logger.info(
            f"🔄 启动 NapCat 转发代理: "
            f"localhost:{local} → {host}:{remote}"
        )

        if blocking:
            self._run_proxy(host, remote, local)
        else:
            self._thread = threading.Thread(
                target=self._run_proxy,
                args=(host, remote, local),
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0):
        """停止转发代理。"""
        self._running = False
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("⏹  NapCat 转发代理已停止")

    def is_alive(self) -> bool:
        """检查代理进程是否存活。"""
        if self._process:
            ret = self._process.poll()
            return ret is None
        return False

    def get_proxy_url(self) -> str:
        """获取转发后的 WebSocket URL。"""
        return f"ws://localhost:{self.config.local_port}"

    # ── 内部: socat 转发 ─────────────────────────────────────

    def _run_proxy(self, host: str, remote_port: int, local_port: int):
        """运行 socat TCP 端口转发。"""
        # 可用工具: socat, ncat, nc
        proxy_cmd = self._build_socat_cmd(host, remote_port, local_port)
        if not proxy_cmd:
            logger.error("❌ 未找到端口转发工具 (socat/ncat/nc)")
            return

        while self._running and self._retry_count < self.config.max_retries:
            try:
                logger.debug(f"执行: {' '.join(proxy_cmd)}")
                self._process = subprocess.Popen(
                    proxy_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                # socat 在前台运行直到断开
                stdout, stderr = self._process.communicate(timeout=3600)
                if self._process.returncode != 0 and self._running:
                    err = stderr.decode("utf-8", errors="replace")
                    logger.warning(
                        f"⚠️  代理连接断开 (code={self._process.returncode}): {err[:200]}"
                    )
                    self._retry_count += 1
                    if self._retry_count < self.config.max_retries:
                        logger.info(
                            f"🔄 重试 ({self._retry_count}/{self.config.max_retries}) "
                            f"等待 {self.config.retry_interval}s..."
                        )
                        time.sleep(self.config.retry_interval)
            except subprocess.TimeoutExpired:
                logger.warning("⚠️  代理进程超时，重启...")
                if self._process:
                    self._process.kill()
                    self._process.wait(timeout=5)
                self._retry_count += 1
            except Exception as e:
                logger.error(f"❌ 代理错误: {e}")
                break

        self._running = False
        if self._retry_count >= self.config.max_retries:
            logger.error("❌ 代理达到最大重试次数，停止")

    def _build_socat_cmd(
        self, host: str, remote_port: int, local_port: int
    ) -> Optional[List[str]]:
        """构建 socat/ncat 端口转发命令。

        优先级: socat > ncat > nc
        """
        # 检查 socat
        socat_path = self._find_binary("socat")
        if socat_path:
            return [
                socat_path,
                f"TCP-LISTEN:{local_port},reuseaddr,fork",
                f"TCP:{host}:{remote_port}",
            ]

        # 检查 ncat
        ncat_path = self._find_binary("ncat")
        if ncat_path:
            return [
                ncat_path,
                "--sh-exec", f"ncat {host} {remote_port}",
                "-l", str(local_port),
                "--keep-open",
            ]

        # 检查 nc（不完全可靠）
        nc_path = self._find_binary("nc")
        if nc_path:
            logger.warning(
                "⚠️  使用 nc 转发，可能不稳定。建议安装 socat: "
                "sudo apt install -y socat"
            )
            return [
                nc_path,
                "-l", "-p", str(local_port),
                "-c", f"nc {host} {remote_port}",
            ]

        return None

    @staticmethod
    def _find_binary(name: str) -> Optional[str]:
        """在 PATH 中查找可执行文件。"""
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            full_path = os.path.join(path_dir, name)
            if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                return full_path
        return None


# ── 便捷函数 ──────────────────────────────────────────────────────

def ensure_napcat_proxy(
    windows_host: str = "",
    remote_port: int = 3001,
    local_port: int = 13001,
) -> NapCatProxy:
    """启动 NapCat 转发代理（自动管理生命周期）。

    如果代理已经在运行则返回现有实例。
    这是推荐的入口函数。

    Returns:
        NapCatProxy 实例（已启动）。
    """
    config = NapCatProxyConfig(
        windows_host=windows_host,
        remote_port=remote_port,
        local_port=local_port,
    )
    proxy = NapCatProxy(config)
    proxy.start()
    return proxy


# ── CLI ───────────────────────────────────────────────────────────

def main():
    """命令行入口: python -m partner.napcat_proxy"""
    import argparse

    parser = argparse.ArgumentParser(
        description="WSL-to-Windows NapCat WebSocket 代理"
    )
    parser.add_argument(
        "--remote-port", type=int, default=3001,
        help="NapCat WebSocket 端口 (Windows 侧)",
    )
    parser.add_argument(
        "--local-port", type=int, default=13001,
        help="本地转发端口 (WSL 侧)",
    )
    parser.add_argument(
        "--host", type=str, default="",
        help="Windows 主机 IP (留空自动检测)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="详细日志",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    config = NapCatProxyConfig(
        windows_host=args.host,
        remote_port=args.remote_port,
        local_port=args.local_port,
    )
    proxy = NapCatProxy(config)

    print(f"  🔄 NapCat 转发代理")
    print(f"  ┌─ localhost:{args.local_port} (WSL)")
    print(f"  └→ {config.windows_host or '(自动检测)'}:{args.remote_port} (Windows)")
    print()
    print("  按 Ctrl+C 停止")

    try:
        proxy.start(blocking=True)
    except KeyboardInterrupt:
        print("\n  ⏹  正在停止...")
    finally:
        proxy.stop()


if __name__ == "__main__":
    main()
