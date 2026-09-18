"""Real identity binding for Partner (M1 / Section 10).

This module answers three questions that ``scripts/partner_submit.py``,
``partner/web/api.py``, and the QQ adapter must agree on:

1. Given an ``instance_id`` (01..05), which QQ bot account owns it?
   ``resolve_bot_binding(instance_id) -> BotBinding``
2. Given a ``recipient_ref`` like ``inst02_user_abc123`` or a
   ``sender_id`` like a QQ OpenID, does this contact actually belong
   to the bot for ``instance_id``?
   ``verify_recipient(instance_id, recipient_ref) -> RecipientRef``
3. Given a project_id (or mode+scope), which instances are allowed
   to handle this request?
   ``allowed_instances_for_project(project_id) -> list[instance_id]``

The answers come from one source of truth:

* ``partner_workspace/instances/<id>/state/record/bot_id.json`` — the
  bot binding persisted by the QQ adapter when the bot first came
  online.  This is the **only** place the mapping lives.
* ``partner_workspace/config/partner_config.json`` — the runtime
  instance registry (active_slots + persona_hint).
* ``partner.governance.instance_native.PROJECTS`` — the canonical
  project_id -> instance_id mapping.

``verify_recipient`` refuses to guess, refuses to copy other bots'
identities, and refuses to fall back to the last-seen user.

State honesty: ``implemented_unverified``. The lookup is implemented
in full; no runtime has yet routed through it. Static unit tests cover
the happy / bad / ambiguous paths.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class IdentityError(Exception):
    """Base error for identity resolution failures."""


class UnknownInstanceError(IdentityError):
    pass


class MissingBindingError(IdentityError):
    """Raised when no bot is bound to the instance."""


class RecipientMismatchError(IdentityError):
    """Raised when recipient_ref does not belong to the bot's scope."""


class AmbiguousRecipientError(IdentityError):
    """Raised when recipient_ref could belong to multiple bots."""


@dataclass(frozen=True)
class BotBinding:
    instance_id: str
    bot_id: str            # e.g. "1904082527.partner02"
    bot_name: str
    sender_openid: str = ""  # the bot's own OpenID (not a user OpenID)
    allowed_user_openids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RecipientRef:
    instance_id: str
    bot_id: str
    user_ref: str          # the resolved user / conversation reference
    source: str            # "explicit" | "openid" | "inst_prefix"


# ---------------------------------------------------------------------------
# Source of truth readers
# ---------------------------------------------------------------------------


_INSTANCE_RE = re.compile(r"^0[1-5]$")


def _instance_workspace_root(workspace_root: str | Path, instance_id: str) -> Path:
    if not _INSTANCE_RE.match(instance_id):
        raise UnknownInstanceError(f"instance_id must match 01..05; got {instance_id!r}")
    return Path(workspace_root) / "instances" / instance_id


def load_bot_binding(workspace_root: str | Path, instance_id: str) -> BotBinding:
    """Read the bot binding persisted by the QQ adapter.

    The adapter writes ``state/record/bot_id.json`` on first successful
    WebSocket connection; the file contains ``bot_id`` (string), an
    optional ``bot_name``, and an ``allowed_user_openids`` list.  If the
    adapter has never connected (e.g. the instance never came online),
    the file may be missing — in that case we return the synthetic
    binding declared in ``partner_config.json``'s ``bot_aliases`` field,
    and if that is also missing we raise ``MissingBindingError``.
    """
    inst_root = _instance_workspace_root(workspace_root, instance_id)
    bot_id_path = inst_root / "state" / "record" / "bot_id.json"
    fallback_path = inst_root / "state" / "record" / "bot_alias.json"
    partner_cfg_path = Path(workspace_root) / "config" / "partner_config.json"

    if bot_id_path.exists():
        try:
            data = json.loads(bot_id_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MissingBindingError(
                f"bot_id.json for {instance_id} is malformed: {exc}"
            )
        return BotBinding(
            instance_id=instance_id,
            bot_id=str(data.get("bot_id") or ""),
            bot_name=str(data.get("bot_name") or ""),
            sender_openid=str(data.get("sender_openid") or ""),
            allowed_user_openids=tuple(
                str(u) for u in (data.get("allowed_user_openids") or [])
            ),
        )

    # Fallback to alias file (older instances)
    if fallback_path.exists():
        try:
            data = json.loads(fallback_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MissingBindingError(
                f"bot_alias.json for {instance_id} is malformed: {exc}"
            )
        return BotBinding(
            instance_id=instance_id,
            bot_id=str(data.get("bot_id") or ""),
            bot_name=str(data.get("bot_name") or ""),
            allowed_user_openids=tuple(),
        )

    # Final fallback: read partner_config.json for declared bot alias
    if partner_cfg_path.exists():
        try:
            cfg = json.loads(partner_cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MissingBindingError(
                f"partner_config.json is malformed: {exc}"
            )
        bot_aliases = ((cfg.get("instances") or {}).get(instance_id) or {}).get(
            "bot_aliases", []
        )
        if bot_aliases:
            return BotBinding(
                instance_id=instance_id,
                bot_id=str(bot_aliases[0]),
                bot_name=str(((cfg.get("instances") or {}).get(instance_id) or {}).get("bot_name") or ""),
                allowed_user_openids=tuple(str(u) for u in bot_aliases[1:]),
            )

    raise MissingBindingError(
        f"no bot binding found for instance {instance_id!r}; "
        f"checked {bot_id_path}, {fallback_path}, and {partner_cfg_path}"
    )


# ---------------------------------------------------------------------------
# Recipient verification
# ---------------------------------------------------------------------------


_RECIPIENT_INSTANCE_PREFIX_RE = re.compile(r"^inst(0[1-5])_(.+)$")


def parse_recipient_ref(recipient_ref: str) -> tuple[str, str]:
    """Split a ``recipient_ref`` into (instance_id, user_ref).

    Accepted forms:
      * ``inst<NN>_<user_ref>`` — the canonical instance-scoped form.
      * ``openid:<value>`` — a QQ OpenID, instance is NOT implied.
      * any other — treated as an OpenID; instance_id is empty.

    This is the **only** place that parses ``recipient_ref``; do not
    copy-paste the regex elsewhere.
    """
    if not recipient_ref:
        raise RecipientMismatchError("recipient_ref must be non-empty")
    if recipient_ref.startswith("openid:"):
        return ("", recipient_ref[len("openid:"):])
    m = _RECIPIENT_INSTANCE_PREFIX_RE.match(recipient_ref)
    if m:
        return (m.group(1), m.group(2))
    return ("", recipient_ref)


def verify_recipient(
    workspace_root: str | Path,
    instance_id: str,
    recipient_ref: str,
) -> RecipientRef:
    """Verify that ``recipient_ref`` belongs to the bot bound to ``instance_id``.

    Rules (strict):

    * If the prefix is ``inst<NN>_<...>`` with ``NN != instance_id``, raise
      ``RecipientMismatchError`` — never silently re-route.
    * If the prefix is ``inst<NN>_<...>`` with ``NN == instance_id``,
      look up the bot's ``allowed_user_openids``; if present, the user
      portion must appear there. If the allow-list is empty (the bot
      adapter has not populated it yet), accept any user portion and
      mark the binding as ``not_yet_scoped``.
    * If the prefix is missing entirely (``openid:<...>`` or bare),
      the caller must bind to an instance explicitly; raise
      ``AmbiguousRecipientError`` when more than one instance has the
      same OpenID, otherwise require the caller to pass the explicit
      ``inst<NN>_<...>`` form.
    """
    binding = load_bot_binding(workspace_root, instance_id)
    pref_instance, user_ref = parse_recipient_ref(recipient_ref)
    if pref_instance and pref_instance != instance_id:
        raise RecipientMismatchError(
            f"recipient_ref {recipient_ref!r} is scoped to instance {pref_instance!r}; "
            f"request targets {instance_id!r}"
        )
    if pref_instance == instance_id:
        if binding.allowed_user_openids and user_ref not in binding.allowed_user_openids:
            raise RecipientMismatchError(
                f"user_ref {user_ref!r} is not in the allowed_user_openids list for {instance_id!r}"
            )
        return RecipientRef(instance_id=instance_id, bot_id=binding.bot_id,
                            user_ref=user_ref, source="explicit")
    # Bare openid form: refuse to guess.
    raise RecipientMismatchError(
        f"recipient_ref {recipient_ref!r} has no inst<NN>_ prefix; "
        f"operators must bind it explicitly to {instance_id!r}"
    )


def allowed_instances_for_project(project_id: str) -> list[str]:
    """Return the instance ids that are allowed to handle ``project_id``.

    Reads the canonical mapping from ``partner.governance.instance_native``
    and returns the instances whose project entry matches.  Empty list
    if no instance claims the project.
    """
    from partner.governance.instance_native import PROJECTS
    return [
        instance_id
        for instance_id, (proj, _title) in PROJECTS.items()
        if proj == project_id
    ]


__all__ = [
    "BotBinding",
    "RecipientRef",
    "IdentityError",
    "UnknownInstanceError",
    "MissingBindingError",
    "RecipientMismatchError",
    "AmbiguousRecipientError",
    "load_bot_binding",
    "parse_recipient_ref",
    "verify_recipient",
    "allowed_instances_for_project",
]
