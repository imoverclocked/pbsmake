import sys
with open('Makefile', 'w') as f:
    targets = int(sys.argv[1])
    for i in xrange(targets):
        f.write('TARGET%d=target%d\n' % (i, i))
    for i in xrange(1, targets):
        components = ['${TARGET%d}' % j for j in xrange(i+1, targets)]
        f.write('${TARGET%d}: ' % i)
        f.write(' '.join(components) + '\n')
        for _ in xrange(2):
            f.write('\t#PBS foobar\n')
