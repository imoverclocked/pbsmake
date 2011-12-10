import os
import re


class Env(object):
    """Maintains the environment variables for local and global scope."""

    def __init__(self, env={}, parent=os.environ):
        self.env = env.copy()
        self.parent = parent.copy()

    def __getitem__(self, key):
        return self.env.get(key) or self.parent[key]

    def __setitem__(self, key, value):
        self.env[key] = value

    def __repr__(self):
        return '%s(%s)' % (type(self).__name__, repr(self.env))

    def interp(self, string, regex=r'\${[a-zA-Z_][a-zA-Z_0-9]*}'):
        match = re.search(regex, string)
        while match:
            start, end = match.span()
            var = string[start + 2 : end - 1]
            string = ''.join((string[:start], self[var], string[end:]))
            match = re.search(regex, string)
        return string


# Based off RADLogic's topsort - http://www.radlogic.com/releases/topsort.py
def buildorder(pairlist):
    num_parents = {}
    children = {}
    for parent, child in pairlist:
        if not num_parents.has_key(parent):
            num_parents[parent] = 0
        if not num_parents.has_key(child):
            num_parents[child] = 0
        num_parents[child] += 1
        children.setdefault(parent, []).append(child)

    ordered = [v for v in num_parents.iterkeys() if num_parents[v] == 0]
    for parent in ordered:
        del num_parents[parent]
        if children.has_key(parent):
            for child in children[parent]:
                num_parents[child] -= 1
                if num_parents[child] == 0:
                    ordered.append(child)
            del children[parent]

    if num_parents:
        raise Exception('dependency cycle detected')
    return reversed(ordered)


def parse(iterable, env=Env()):
    handlers = {}
    class pattern(object):
        def __init__(self, regex):
            self.regex = regex
        def __call__(self, function):
            handlers[self.regex] = function
            def wrap(*args, **kwds):
                function(*args, **kwds)
            return wrap

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9]*)\s*=\s*(.+)$')
    def vardecl(match, env=env):
        name, value = match.groups()
        env[name] = value
        return name, value

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9\{\}\.]*)\s*:\s*(.*)$')
    def target(match, env=env):
        interpolated = [env.interp(m) for m in match.groups() if m]
        name = interpolated[0]
        components = []
        if len(interpolated) > 1:   # we have components to parse
            line = ' '.join(word for word in interpolated[1:])
            match = re.search('(\w+)', line)
            components = match.groups()
        return name, components

    @pattern(r'^\s*\#\s*(.*)')
    def comment(match, env=env):
        line = match.group(1)
        return '# ' + line

    @pattern(r'^\t(.+)$')
    def command(match, env=env):
        cmd = match.group(1)
        return '\tcmd(%s)' % cmd

    def notimplemented(*args):
        return 'not implemented: ' + ', '.join(args)

    for line in iterable:
        result = None
        for pattern, function in handlers.iteritems():
            match = re.match(pattern, line)
            if match is not None:
                result = function(match)
                break
        if result is None:
            result = notimplemented(line)
        print result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('target', help='The target to build', nargs='+')
    parser.add_argument('-f', '--makefile', default='Makefile')
    args = parser.parse_args()

    with open(args.makefile) as f:
        makefile = (line.rstrip() for line in f.readlines() if line.strip())
        parse(makefile)


# vim: ts=4 sw=4 et :
