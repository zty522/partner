"""Partner core package.

Keep the public ``Partner`` export lazy. Importing a leaf module such as
``partner.core.delivery_queue`` must not initialize the whole runtime: the
runtime imports ``partner.mind`` in turn, which otherwise creates an
order-dependent circular import.
"""

from typing import Any

__all__ = ["Partner"]


def __getattr__(name: str) -> Any:
    if name == "Partner":
        from .core import Partner

        return Partner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
