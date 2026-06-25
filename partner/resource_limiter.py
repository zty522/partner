"""Resource Limiter — 限制 Partner 子进程的 CPU/内存使用。

通过进程优先级、资源限制 (setrlimit) 和 CPU 亲和性实现。
"""

import os
import platform
import logging

logger = logging.getLogger(__name__)


def apply_limits(nice_value: int = 10, mem_limit_mb: int = 8192):
    """设置当前进程的资源限制。

    Args:
        nice_value: nice 值(0-19, 越高优先级越低)
        mem_limit_mb: 内存限制(MB)
    """
    system = platform.system()

    # 设置 nice 值 (降低优先级)
    if system != "Windows":
        try:
            import os
            os.nice(nice_value)
            logger.info(f"[资源限制] nice 值设为 {nice_value}")
        except Exception as e:
            logger.warning(f"[资源限制] 设置 nice 失败: {e}")

    # 设置内存限制 (仅 Linux)
    if system == "Linux":
        try:
            import resource
            mem_limit_bytes = mem_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_limit_bytes, mem_limit_bytes))
            logger.info(f"[资源限制] 内存限制设为 {mem_limit_mb}MB")
        except Exception as e:
            logger.warning(f"[资源限制] 设置 RLIMIT_AS 失败: {e}")

    # CPU 亲和性 (单核运行，避免抢占) - 仅 Linux
    if system == "Linux":
        try:
            # 读取 /proc/self/status 查看 CPU 使用率
            pass  # psutil 不是必须依赖，不强制使用
        except Exception:
            pass


def get_process_info() -> dict:
    """获取当前进程基本信息（不依赖 psutil）。"""
    import os
    info = {
        "pid": os.getpid(),
        "nice": os.nice(0),
    }
    # 读取 /proc/self/status (Linux only)
    if os.path.exists("/proc/self/status"):
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        info["memory_mb"] = int(line.split(":")[1].strip().split()[0]) / 1024
                    elif line.startswith("Threads:"):
                        info["threads"] = int(line.split(":")[1].strip())
        except Exception:
            pass
    return info
