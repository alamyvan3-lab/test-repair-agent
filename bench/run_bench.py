"""Benchmark entry point; same as `test-repair bench`. Run `python bench/run_bench.py --help`."""

import sys

from test_repair.benchmark import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
