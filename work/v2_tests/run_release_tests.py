"""Run portable, offline CPU tests; optionally run the OpenCV worker tests."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'work/v2_source'), str(HERE)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT / 'test-results.json')
    parser.add_argument('--portrait-python', type=Path,
                        help='Python containing cv2 and numpy for the four portrait tests.')
    args = parser.parse_args()
    local_temp = ROOT / 'work/temp'
    local_temp.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(local_temp)
    start = time.monotonic()
    modules = sorted(path.stem for path in HERE.glob('test_*.py')
                     if path.stem != 'test_portrait_detail')
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    worker_python = str(args.portrait_python or Path(sys.executable))
    check = subprocess.run([worker_python, '-c', 'import cv2,numpy'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    worker = None
    if check.returncode == 0:
        worker = subprocess.run([worker_python, '-B', str(HERE / 'test_portrait_detail.py')],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        print(worker.stdout.decode('utf-8', errors='replace'), flush=True)
    else:
        print('SKIPPED: Four portrait tests require cv2 and numpy. Use --portrait-python or install them.')
    passed = result.wasSuccessful() and (worker is None or worker.returncode == 0)
    report = dict(status=('PASS' if worker is not None else 'PASS_WITH_PORTRAIT_SKIPPED') if passed else 'FAIL',
                  application_tests_run=result.testsRun, application_failures=len(result.failures),
                  application_errors=len(result.errors), application_skipped=len(result.skipped),
                  application_skip_reasons=[dict(test=test.id(), reason=reason)
                                            for test, reason in result.skipped],
                  portrait_worker_exit_code=worker.returncode if worker else None,
                  portrait_worker_tests_run=4 if worker is not None else 0,
                  portrait_worker_output=worker.stdout.decode('utf-8', errors='replace') if worker else '',
                  application_modules=modules, elapsed_seconds=time.monotonic() - start,
                  note='Offline CPU fixtures; no AI model, GPU, account or paid request is used.')
    path = args.report.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(report['status'], path)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
