import sys
import pstats

p = pstats.Stats(sys.argv[1])
print p.strip_dirs().sort_stats('time').print_stats(10)
