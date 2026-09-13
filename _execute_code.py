import sys; sys.path.insert(0, '/mnt/e/work/partner')
import subprocess
result = subprocess.run(['python','-m','pytest','-q','tests/','--tb=line','--maxfail=20'], cwd='/mnt/e/work/partner', capture_output=True, text=True, timeout=180)
print('RC:', result.returncode)
print('STDOUT_TAIL_4000:', result.stdout[-4000:])
print('STDERR_TAIL_2000:', result.stderr[-2000:])