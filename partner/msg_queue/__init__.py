"""Partner Queue — message broker and dispatcher for cross-instance task routing.

WARNING: This directory shadows Python's stdlib 'queue' module.
To avoid breaking code that needs queue.SimpleQueue etc., we add
a forwarding shim here that makes stdlib queue's names available
through this package.

HOWEVER, the long-term fix is to rename this directory to avoid
shadowing entirely.
"""

import sys as _sys

# Trick to import stdlib queue despite our own directory
# Save our module reference, remove from sys.modules, import stdlib, restore
_our_module = _sys.modules.get(__name__)
_sys.modules.pop(__name__, None)

try:
    # Now 'import queue' will find the stdlib one
    import queue as _stdlib_queue
finally:
    # Restore ourselves as the module
    if _our_module is not None:
        _sys.modules[__name__] = _our_module

# Copy all public names from stdlib queue into our namespace
# This way `import queue; queue.SimpleQueue()` works
for _name in dir(_stdlib_queue):
    if not _name.startswith('_'):
        setattr(_our_module, _name, getattr(_stdlib_queue, _name))
del _stdlib_queue, _name
