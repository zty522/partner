#!/usr/bin/env python3
from argparse import ArgumentParser
from partner.benchmark.v4_matrix import write_matrix
p=ArgumentParser(); p.add_argument('suite_dirs',nargs='+'); p.add_argument('--output',required=True)
a=p.parse_args(); print(write_matrix(a.suite_dirs,a.output))
