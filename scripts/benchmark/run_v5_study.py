#!/usr/bin/env python3
"""Run one frozen Partner v5 study through the public benchmark wrapper."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from partner.benchmark.v5_protocol import load_and_validate
from partner.benchmark.wrapper import PartnerBenchmarkWrapper


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--workspace",default="/mnt/e/work/partner_workspace")
    parser.add_argument("--study",required=True)
    parser.add_argument("--instance",default="01")
    parser.add_argument("--project-id",default="partner_v5_research")
    parser.add_argument("--timeout",type=float,default=1800)
    parser.add_argument("--channel",default="local",choices=["local","web","qq","both"])
    args=parser.parse_args(); study=Path(args.study).resolve(); load_and_validate(study)
    wrapper=PartnerBenchmarkWrapper(args.workspace)
    submission=wrapper.submit(protocol_id="v5_open_generalization_study_v1",
        request="/benchmark v5_open_generalization_study_v1\nRun the frozen v5 cross-project study.",
        instance_id=args.instance,project_id=args.project_id,inputs={"study_path":str(study)},
        channel=args.channel,sender_id="v5-benchmark-wrapper")
    print(json.dumps(submission.__dict__,ensure_ascii=False))
    result=wrapper.wait(submission,timeout_seconds=args.timeout)
    print(json.dumps(result,ensure_ascii=False))
    if result.get("status")!="completed": raise SystemExit(1)

if __name__=="__main__": main()
