"""Sandbox — Partner 中心化沙箱服务。

提供代码隔离执行和验证能力，供 batch_planner、自进化等模块调用。

用法：
    from partner.sandbox.service import SandboxService, SandboxContext, IsolationLevel

    sbx = SandboxService(workspace="/path/to/workspace")

    # 验证代码是否可执行
    result = await sbx.validate_code("print('hello')")

    # 实际执行代码
    result = await sbx.execute_code("print('hello')")
    print(result.stdout)
"""
from .service import SandboxService, SandboxContext, SandboxResult, IsolationLevel, SandboxMode

__all__ = [
    "SandboxService",
    "SandboxContext",
    "SandboxResult",
    "IsolationLevel",
    "SandboxMode",
]
