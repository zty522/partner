import sys; sys.path.insert(0, '/mnt/e/work/partner')
import sys, json
sys.path.insert(0, '/mnt/e/work/partner')
from partner.governance.shadow_replay import evaluate_isolated_preflight_canary
result = evaluate_isolated_preflight_canary(
    workspace='/mnt/e/work/partner_workspace',
    project_id='literature_github_learning',
    experiment_id='experiment_7736f187bcad',
)
print(json.dumps({'pairs': result.get('pairs'), 'decision': result.get('decision'),
                  'promotion': result.get('promotion'), 'metrics': result.get('metrics')}, indent=2))
