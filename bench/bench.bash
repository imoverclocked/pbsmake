#!/usr/bin/env bash
python makefilegen.py 500
python -m cProfile -o pbsmake.prof ../pbsmake.py -f Makefile target1 >/dev/null
python dumpprof.py pbsmake.prof
rm -f Makefile pbsmake.prof
