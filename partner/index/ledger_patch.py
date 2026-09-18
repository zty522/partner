"""Compatibility shims; EventLedger owns indexed reads directly."""
def _install_on_instance(repo_root):
    return lambda ledger: True

def install_fast_path(repo_root):
    return True

install_history_fast_path = install_fast_path

def install_at_event_worker_init():
    return True

install_at_event_worker_init_old = install_fast_path
