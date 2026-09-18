"""Identity resolution for Partner (M1).

See ``binding.py`` for the indexed lookups and the strict-recipient
contract.
"""
from .binding import (
    BotBinding, RecipientRef, IdentityError, UnknownInstanceError,
    MissingBindingError, RecipientMismatchError, AmbiguousRecipientError,
    load_bot_binding, parse_recipient_ref, verify_recipient,
    allowed_instances_for_project,
)
__all__ = [
    "BotBinding", "RecipientRef", "IdentityError", "UnknownInstanceError",
    "MissingBindingError", "RecipientMismatchError", "AmbiguousRecipientError",
    "load_bot_binding", "parse_recipient_ref", "verify_recipient",
    "allowed_instances_for_project",
]
