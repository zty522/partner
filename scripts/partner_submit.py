#!/usr/bin/env python3
"""Single-instance submission CLI (M1 / Section 4 + 3.3) — round 5.

CLI wrapper for ``partner.application.orchestrator.orchestrate_submit``.
Translates orchestrator errors into exit codes; otherwise prints a
JSON envelope with the SubmissionResult fields.

State honesty: ``static_implemented``.  Not executed in this session.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from partner.application.orchestrator import (
    IdempotencyConflict, IdempotencyScopeError, NotYetOwned,
    UnauthorisedInstanceError,
)


class SubmitScriptError(Exception):
    pass


class ValidationError(SubmitScriptError):
    pass


class NotFoundError(SubmitScriptError):
    pass


class ServiceUnavailable(SubmitScriptError):
    pass


@dataclass(frozen=True)
class SubmitPayload:
    instance: str
    sender_id: str
    sender_name: str
    message: str
    reply_to: str
    conversation_id: str | None
    project_id: str | None
    mode: str | None
    scope: str | None
    request_id: str | None
    recipient_ref: str | None
    constraints_file: str | None
    execution_constraints: dict
    attachments_signature: list = field(default_factory=list)
    subject_allowed_instances: list = field(default_factory=list)
    subject_id: str = ""
    # direct_answer=True means "this submission is a direct answer, do not
    # use the idempotency machinery even if a request_id is supplied".
    direct_answer: bool = False


_VALID_INSTANCES = {"01", "02", "03", "04", "05"}
_VALID_REPLY_TO = {"web", "qq", "both"}


def _check_instance(instance: str) -> None:
    if instance not in _VALID_INSTANCES:
        raise ValidationError(
            f"--instance must be one of {sorted(_VALID_INSTANCES)}; got {instance!r}"
        )


def _check_reply_to(reply_to: str) -> None:
    if reply_to not in _VALID_REPLY_TO:
        raise ValidationError(
            f"--reply-to must be one of {sorted(_VALID_REPLY_TO)}; got {reply_to!r}"
        )


def _check_project_vs_mode(project_id, mode):
    if project_id and mode:
        raise ValidationError("--project-id and --mode are mutually exclusive")
    if not project_id and not mode:
        raise ValidationError("one of --project-id or --mode is required")
    if mode and mode not in {"learning_improvement", "self_improvement", "project_iteration", "direct_answer"}:
        raise ValidationError(
            f"--mode invalid: {mode!r}"
        )


def _check_message(message: str, message_file: str | None) -> str:
    if message and message_file:
        raise ValidationError("--message and --message-file are mutually exclusive")
    if message_file:
        path = Path(message_file)
        if not path.exists():
            raise NotFoundError(f"--message-file does not exist: {message_file}")
        text = path.read_text(encoding="utf-8")
    else:
        text = message
    if not text or not text.strip():
        raise ValidationError("message body must be non-empty")
    return text


def _read_constraints_canonical(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise NotFoundError(f"--constraints-file does not exist: {path}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise NotFoundError(f"constraints file unreadable: {exc}")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"constraints file is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        raise ValidationError("constraints file must be a JSON object")
    return raw


def _check_request_id(request_id: str | None, *, direct_answer: bool) -> None:
    if direct_answer:
        return
    if not request_id or len(request_id) < 8:
        raise ValidationError(
            "--request-id must be at least 8 characters unless --direct-answer is set"
        )


def _channel_for(reply_to: str) -> str:
    return "qq" if reply_to in {"qq", "both"} else "web"


def compute_fingerprint(payload: SubmitPayload) -> str:
    from partner.application.orchestrator import compute_fingerprint as _fp
    return _fp(
        instance=payload.instance, message=payload.message,
        project_id=payload.project_id, mode=payload.mode,
        scope=payload.scope, sender_id=payload.sender_id,
        reply_to=payload.reply_to, recipient_ref=payload.recipient_ref,
        attachments=payload.attachments_signature,
        execution_constraints=payload.execution_constraints,
    )


def submit(payload: SubmitPayload, workspace_root: str) -> dict:
    """Call the unified orchestrator.  Returns a dict-shaped result."""
    from partner.application.orchestrator import orchestrate_submit
    try:
        result = orchestrate_submit(
            workspace_root=workspace_root,
            text=payload.message,
            channel=_channel_for(payload.reply_to),
            sender_id=payload.sender_id,
            sender_name=payload.sender_name,
            persona_hint=payload.instance,
            project_id=payload.project_id or "",
            mode=payload.mode or "",
            scope=payload.scope or "",
            execution_constraints=payload.execution_constraints,
            attachments=None,
            report_policy="milestone",
            request_id=None if payload.direct_answer else payload.request_id,
            recipient_ref=payload.recipient_ref,
            subject_allowed_instances=payload.subject_allowed_instances,
            subject_id=payload.subject_id,
            attachments_signature=payload.attachments_signature,
        )
    except IdempotencyConflict as exc:
        raise
    except (IdempotencyScopeError, NotYetOwned) as exc:
        # In-progress replay / owner-token mismatch — caller can retry.
        raise
    except UnauthorisedInstanceError as exc:
        raise
    return {
        "job_id": result.job_id,
        "assigned_instance": result.assigned_instance,
        "persona_hint": result.persona_hint,
        "project_id": result.project_id,
        "status": result.status,
        "route": result.route,
        "request_id": result.request_id,
        "fingerprint": result.fingerprint,
        "owner_token": result.owner_token,
        "was_idempotent_hit": result.was_idempotent_hit,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="partner_submit",
        description="Single-instance Partner submission CLI (M1 / Section 4).",
    )
    p.add_argument("--instance", required=True, help="01..05.")
    p.add_argument("--project-id", default=None)
    p.add_argument("--mode", default=None,
                   help="learning_improvement | self_improvement | "
                        "project_iteration | direct_answer.")
    p.add_argument("--scope", default=None)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--message", default=None)
    g.add_argument("--message-file", default=None)
    p.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    p.add_argument("--reply-to", default="web",
                   choices=sorted(_VALID_REPLY_TO))
    p.add_argument("--conversation-id", default=None)
    p.add_argument("--recipient-ref", default=None)
    p.add_argument("--request-id", default=None,
                   help=">=8 chars unless --direct-answer.")
    p.add_argument("--direct-answer", action="store_true",
                   help="Treat this as a direct answer (no idempotency).")
    p.add_argument("--constraints-file", default=None)
    p.add_argument("--subject-id", default="cli-user")
    p.add_argument("--subject-allowed-instances", default=None,
                   help="Comma-separated; default = --instance.")
    p.add_argument("--attachments-signature", default=None,
                   help="JSON list of {name, sha256, size, type}; "
                        "becomes part of fingerprint.")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--preview", action="store_true",
                        help="Validate only; no side effects.")
    action.add_argument("--submit", action="store_true")
    return p


def build_payload(args: argparse.Namespace) -> SubmitPayload:
    _check_instance(args.instance)
    _check_reply_to(args.reply_to)
    _check_project_vs_mode(args.project_id, args.mode)
    _check_request_id(args.request_id, direct_answer=args.direct_answer)
    text = _check_message(args.message, args.message_file)
    execution_constraints = _read_constraints_canonical(args.constraints_file)
    allowed = (args.subject_allowed_instances.split(",")
               if args.subject_allowed_instances else [args.instance])
    attachments_sig = []
    if args.attachments_signature:
        try:
            attachments_sig = json.loads(args.attachments_signature)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"bad --attachments-signature: {exc}")
    return SubmitPayload(
        instance=args.instance,
        sender_id="cli:" + args.subject_id,
        sender_name=args.subject_id,
        message=text,
        reply_to=args.reply_to,
        conversation_id=args.conversation_id,
        project_id=args.project_id,
        mode=args.mode,
        scope=args.scope,
        request_id=args.request_id,
        recipient_ref=args.recipient_ref,
        constraints_file=args.constraints_file,
        execution_constraints=execution_constraints,
        attachments_signature=attachments_sig,
        subject_allowed_instances=allowed,
        subject_id=args.subject_id,
        direct_answer=args.direct_answer,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        payload = build_payload(args)
    except SubmitScriptError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc),
                          "request_id": args.request_id}))
        return 2

    fingerprint = compute_fingerprint(payload)

    if args.preview:
        # Preview still validates recipient scoping: a recipient_ref scoped to
        # a DIFFERENT instance must be rejected even without a live bot binding.
        if payload.recipient_ref:
            from partner.identity.binding import parse_recipient_ref, RecipientMismatchError
            try:
                pref_instance, _user_ref = parse_recipient_ref(payload.recipient_ref)
            except RecipientMismatchError as exc:
                print(json.dumps({"status": "rejected", "error": str(exc),
                                  "request_id": payload.request_id}))
                return 3
            if pref_instance and pref_instance != payload.instance:
                print(json.dumps({
                    "status": "rejected",
                    "error": f"recipient_ref scoped to instance {pref_instance!r}; "
                             f"request targets {payload.instance!r}",
                    "request_id": payload.request_id,
                }))
                return 3
        out = {
            "status": "preview",
            "accepted": False, "queued": False,
            "job_id": None, "assigned_instance": None,
            "persona_hint": payload.instance,
            "flow_id": None, "web_view_url": None,
            "delivery": {ch: ("pending" if ch in payload.reply_to else "n_a")
                         for ch in ("qq", "web")},
            "request_id": payload.request_id,
            "payload_fingerprint": fingerprint,
            "owner_token": None,
            "warnings": ["preview mode: no Job, no inbox, no LLM, no message."],
            "error": None,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    try:
        result = submit(payload, args.workspace)
    except IdempotencyConflict as exc:
        print(json.dumps({"status": "rejected",
                          "error": f"idempotency_conflict: {exc}",
                          "request_id": payload.request_id,
                          "payload_fingerprint": fingerprint}))
        return 1
    except (IdempotencyScopeError, NotYetOwned) as exc:
        print(json.dumps({"status": "in_progress",
                          "error": str(exc),
                          "request_id": payload.request_id,
                          "payload_fingerprint": fingerprint}))
        return 1
    except UnauthorisedInstanceError as exc:
        print(json.dumps({"status": "rejected", "error": f"unauthorised: {exc}",
                          "request_id": payload.request_id,
                          "payload_fingerprint": fingerprint}))
        return 5
    except ServiceUnavailable as exc:
        print(json.dumps({"status": "error", "error": str(exc),
                          "request_id": payload.request_id,
                          "payload_fingerprint": fingerprint}))
        return 4

    out = {
        "status": ("queued" if not result["was_idempotent_hit"]
                   else "idempotent_replay"),
        "accepted": True,
        "queued": not result["was_idempotent_hit"],
        "job_id": result["job_id"],
        "assigned_instance": result["assigned_instance"] or payload.instance,
        "persona_hint": result["persona_hint"],
        "flow_id": None,
        "web_view_url": f"/api/jobs/{result['job_id']}" if result["job_id"] else None,
        "delivery": {
            "qq": "pending" if payload.reply_to in {"qq", "both"} else "n_a",
            "web": "pending" if payload.reply_to in {"web", "both"} else "n_a",
        },
        "request_id": payload.request_id,
        "payload_fingerprint": fingerprint,
        "owner_token": result.get("owner_token") or None,
        "was_idempotent_hit": result["was_idempotent_hit"],
        "warnings": [],
        "error": None,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
