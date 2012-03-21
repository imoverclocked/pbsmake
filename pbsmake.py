#!/usr/bin/env python

import os
import re
import sys
import copy
import collections
import functools
import itertools
import operator
import subprocess
import tempfile 

import pbs


class Env(object):
    def __init__(self, env={}, parent=os.environ):
        self.env = env.copy()
        self.parent = parent.copy()

    def __getitem__(self, key):
        return self.env.get(key) or self.parent[key]

    def __setitem__(self, key, value):
        self.env[key] = value

    def asdict(self):
        d = copy.deepcopy(self.parent)
        d.update(self.env)
        return d

    def deepcopy(self):
        e = copy.deepcopy(self.env)
        p = copy.deepcopy(self.parent)
        return Env(e, p)

    def setdefault(self, key, value):
        return self.env.setdefault(key, value)

    def interp(self, string, regex=r'(?<!\\)\${[a-zA-Z_][a-zA-Z_0-9]*}'):
        match = re.search(regex, string)
        while match:
            start, end = match.span()
            var = string[start + 2:end - 1]
            string = ''.join((string[:start], self[var], string[end:]))
            match = re.search(regex, string)
        return string


# RADLogic's topsort - http://www.radlogic.com/releases/topsort.py
def tsort(pairlist):
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
    return ordered


class Makefile(object):
    def __init__(self):
        self.targets = collections.defaultdict(list)
        self.default = ''

    @staticmethod
    def canonicalize(name):
        return re.sub('::afterok', '', name)

    def addtarget(self, name, components=None, cmds=None):
        name = self.canonicalize(name)
        if not self.default and '%' not in name:
            self.default = name
        self.current = name
        self.targets[name] = collections.defaultdict(list)
        self.addcomponents(name, components)
        self.addcmds(name, cmds)

    def addcmds(self, name, cmds):
        if isinstance(cmds, basestring):
            cmds = [cmds]
        self.targets[name]['cmds'] += cmds or []

    def addcomponents(self, name, components):
        if isinstance(components, basestring):
            components = [components]
        components = map(self.canonicalize, components or [])
        self.targets[name]['components'] += components

    def build(self, buildtarget=None):
        buildtarget = self.canonicalize(buildtarget) or self.default
        targets = copy.deepcopy(self.targets)

        wildcard, minmatch = '', 1e99
        for name in targets:
            if '%' in name:
                regex = name.replace('%', '(\w+)', 1)
                match = re.search(regex, buildtarget)
                if match and len(match.group(1)) < minmatch:
                    wildcard = match.group(1)
                    minmatch = len(wildcard)

        for name in tuple(targets):
            if '%' in name:
                resolved = name.replace('%', wildcard, 1)
                targets[resolved] = targets[name]
                targets[name]['pm_target_match'] = wildcard
                del targets[name]
        assert buildtarget in targets

        for name in targets:
            subenv = self.env.deepcopy()
            subenv['pm_target_name'] = name
            targets[name]['env'] = subenv
            if 'pm_target_match' in targets[name]:
                subenv['pm_target_match'] = targets[name]['pm_target_match']

                def interp(cmd):
                    if cmd[:4] == '#PBS':
                        cmd = subenv.interp(cmd)
                    return cmd

                cmds = map(interp, targets[name]['cmds'])
                default = '#PBS -S /bin/sh'
                pos = 1 if cmds[0][:2] == '#!' else 0
                cmds.insert(pos, default)
                targets[name]['cmds'] = cmds

        pairlist = []
        for target, details in targets.iteritems():
            for component in details['components']:
                pairlist.append((target, component))

        order = tsort(pairlist)
        not1 = functools.partial(operator.ne, buildtarget)
        schedule = list(itertools.dropwhile(not1, order))
        if buildtarget not in schedule:
            schedule = [buildtarget]
        schedule.reverse()

        def submit(name, lastid=None):
            target = targets[name]
            subenv = target['env'].asdict()
            if self.local:
                lastid = 'local'
                for cmd in target['cmds']:
                    if not cmd.startswith('#'):
                        subprocess.call(cmd, env=subenv, shell=True)
            else:
                with tempfile.NamedTemporaryFile() as taskfile:
                    taskfile.write('\n'.join(cmd for cmd in target['cmds']))
                    taskfile.flush()
                    varlist = ','.join('%s=%s' % (k,v) for k,v in subenv.iteritems())
                    if lastid:
                        attropl = pbs.new_attropl(3)
                        attropl[0].name = pbs.ATTR_N
                        attropl[0].value = 'pbsmake-%s' % name
                        attropl[1].name = pbs.ATTR_v
                        attropl[1].value = varlist
                        attropl[2].name = pbs.ATTR_depend
                        dep = name.partition('::')[-1] or 'afterok'
                        attropl[2].value = 'depend=%s:%s' % (dep, lastid)
                    else:
                        attropl = pbs.new_attropl(2)
                        attropl[0].name = pbs.ATTR_N
                        attropl[0].value = 'pbsmake-%s' % name
                        attropl[1].name = pbs.ATTR_v
                        attropl[1].value = varlist
                    lastid = pbs.pbs_submit(conn, attropl, taskfile.name, '', '')
                    target['torqueid'] = lastid
            return '%s(%s) scheduled' % (name, lastid)


        srvname = pbs.pbs_default()
        conn = pbs.pbs_connect(srvname)

        for name in schedule:
            print submit(name)

        for name in targets.iterkeys():
            if '::' in name and name not in schedule:
                parent = re.sub('::.+', '', target)
                if parent in schedule:
                    torqueid = targets[parent]['torqueid']
                    print submit(name, torqueid)


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

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9]*)\s*=\s*(?:[\"\'])?(.+?)(?:[\"\'])?$')
    def vardecl(match, env=env):
        name, value = match.groups()
        env[name] = env.interp(str(value))
        return name + '=' + value

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9]*)\s*\+=\s*(?:[\"\'])?(.+?)(?:[\"\'])?$')
    def varapdecl(match, env=env):
        name, value = match.groups()
        env[name] += env.interp(str(value))
        return name + '+=' + value

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9]*)\s*\?=\s*(?:[\"\'])?(.+?)(?:[\"\'])?$')
    def varcondecl(match, env=env):
        name, value = match.groups()
        env.setdefault(name, env.interp(str(value)))
        return name + '?=' + value

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9\{\}\%\/\.-]*)\s*:(?::([a-zA-Z_\$][a-zA-Z_0-9\{\}]*):)?\s*(.*)$')
    def target(match, env=env):
        labels = ('name', 'dep', 'components')
        groups = dict(itertools.izip(labels, match.groups()))
        for k, v in groups.iteritems():
            groups[k] = env.interp(v) if v else v
        name = groups['name']
        dep = groups['dep'] or 'afterok'
        components = re.findall('\S+', groups['components'])
        makefile.addtarget(name + '::' + dep, components)
        return '%s::%s: %s' % (name, dep, ' '.join(str(c) for c in components))

    @pattern(r'^\#\s*(.*)')
    def comment(match, env=env):
        line = match.group(1)
        return '# ' + line

    @pattern(r'^\t(.+)$')
    def command(match, env=env):
        cmd = match.group(1)
        makefile.addcmds(makefile.current, cmd)
        return '\t' + cmd

    makefile = Makefile()
    for line in iterable:
        result = None
        for pattern, function in handlers.iteritems():
            match = re.match(pattern, line)
            if match is not None:
                result = function(match)
                #print '%-10s: %s' % (function.__name__, result)
                break
        if result is None:
            raise NotImplementedError(line)
    makefile.env = env
    return makefile


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('target', help='The target to build', nargs='*')
    parser.add_argument('-f', '--makefile', default='Makefile')
    parser.add_argument('-l', '--local', default=False, action='store_true')
    args = parser.parse_args()

    with open(args.makefile) as f:
        contents = (line.rstrip() for line in f.readlines() if line.strip())
        makefile = parse(contents)
        makefile.local = args.local
        if not args.target and makefile.default:
            args.target = [makefile.default]
        for target in args.target:
            makefile.build(target)


# vim: ts=4 sw=4 et :
