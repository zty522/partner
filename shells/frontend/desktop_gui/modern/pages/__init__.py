"""Page widgets for the modern Partner GUI."""

from .chat import ChatPage
from .instances import InstancesPage
from .settings import SettingsPage
from .agents import AgentsPage
from .setup_wizard import SetupWizardPage

__all__ = [
    "ChatPage",
    "InstancesPage",
    "SettingsPage",
    "AgentsPage",
    "SetupWizardPage",
]