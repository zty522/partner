#!/usr/bin/env bash
# Partner Skill CLI - Wrapper script
# Usage: partner-skill register --name <name> [options]
export PYTHONPATH="${PYTHONPATH}:$(dirname $(dirname $(readlink -f $0)))"
python3 -m partner.skills.cli "$@"
