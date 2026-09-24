#!/usr/bin/env python3
"""Submit a real Partner benchmark through the public Event/Flow runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from partner.benchmark.wrapper import PartnerBenchmarkWrapper  # noqa: E402


def pairs(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected key=value: {value}")
        key, item = value.split("=", 1)
        result[key] = item
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--instance", default="01")
    parser.add_argument("--project", required=True)
    parser.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--guardrail", action="append", default=[], metavar="KEY=true|false")
    parser.add_argument("--allow-external-judges", action="store_true")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    guardrails = {key: value.lower() == "true" for key, value in pairs(args.guardrail).items()}
    wrapper = PartnerBenchmarkWrapper(args.workspace)
    submitted = wrapper.submit(
        protocol_id=args.protocol, request=args.request, instance_id=args.instance,
        project_id=args.project, inputs=pairs(args.input),
        guardrail_results=guardrails,
        allow_external_judges=args.allow_external_judges)
    payload: dict[str, Any] = dict(submitted.__dict__)
    if args.wait and submitted.accepted:
        payload["job"] = wrapper.wait(submitted)
        payload["result"] = wrapper.result(submitted.benchmark_run_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if submitted.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
