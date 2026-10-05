#!/usr/bin/env python3
"""Run one frozen Partner v4 suite through the public benchmark wrapper."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from partner.benchmark.v4_protocol import load_and_validate
from partner.benchmark.wrapper import PartnerBenchmarkWrapper


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--workspace',default='/mnt/e/work/partner_workspace')
    ap.add_argument('--suite',required=True)
    ap.add_argument('--instance',default='01')
    ap.add_argument('--project-id',default='partner_v4_benchmark')
    ap.add_argument('--timeout',type=float,default=1800)
    ap.add_argument('--channel',default='local',choices=['local','web','qq','both'])
    ap.add_argument('--sender-id',default='benchmark-wrapper')
    ns=ap.parse_args()
    suite=Path(ns.suite).resolve(); load_and_validate(suite)
    wrapper=PartnerBenchmarkWrapper(ns.workspace)
    sub=wrapper.submit(protocol_id='v4_longitudinal_closed_loop_v1',
        request='/benchmark v4_longitudinal_closed_loop_v1\nRun one frozen longitudinal suite.',
        instance_id=ns.instance,project_id=ns.project_id,inputs={'suite_path':str(suite)},
        channel=ns.channel,sender_id=ns.sender_id)
    print(json.dumps(sub.__dict__,ensure_ascii=False))
    result=wrapper.wait(sub,timeout_seconds=ns.timeout)
    print(json.dumps(result,ensure_ascii=False))
    if result.get('status')!='completed': raise SystemExit(1)

if __name__=='__main__': main()
