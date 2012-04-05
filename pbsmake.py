#!/usr/bin/env python

import argparse
import collections
import copy
import itertools
import os
import pbs
import re
import subprocess
import sys
import tempfile 


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

        if buildtarget not in targets:
            # If any top-level target name includes a '%', then we
            # need to attempt to resolve the wildcard.
            if any('%' in name for name in targets):
                # Find longest wildcard match of all wildcard targets.
                wildcard, minmatch = '', 1e99
                for name in targets:
                    if '%' in name:
                        regex = name.replace('%', '(\w+)', 1)
                        match = re.search(regex, buildtarget)
                        if match and len(match.group(1)) < minmatch:
                            wildcard = match.group(1)
                            minmatch = len(wildcard)

                for name in targets:
                    if '%' in name:
                        resolved = name.replace('%', wildcard, 1)
                        # We need to check if the resolved name is also the name
                        # of an existing static target. The convention is not to
                        # override a static target with the resolved dynamic one.
                        if resolved in targets:
                            continue
                        # Otherwise, the resolved dynamic target satisfies a dynamic
                        # rule and we need to save the wildcard match.
                        targets[resolved] = targets[name]
                        targets[resolved]['pm_target_match'] = wildcard
                        # Deleting the original name, which contains the '%' wildcard,
                        # ensures that during the dependency graph creation we have
                        # real target names based off the buildtarget. That is,
                        # a resolved 'generic-%' with buildtarget 'foo' will delete
                        # 'generic-%' in the targets dictionary and replace it with
                        # 'generic-foo' for graph analysis.
                        del targets[name]

        # Assert that the desired buildtarget is in our completely resolved, as
        # of the last step, targets dictionary.
        assert buildtarget in targets

        # Traverse all top-level targets and attempt to resolve their wildcard
        # dependencies. This must be done because the toplogical sort requires
        # that there be no wildcard targets. An optimization could be made to
        # embed the topological sort in this step and minimize the amount of
        # traversals. Currently, the entire makefile must be resolved before
        # sorting, regardless if the buildtarget has no components.
        wildcards = set()
        for target in filter(lambda name: '%' not in name, targets):
            for component in targets[target]['components']:
                if component not in targets:
                    wildcard, matchtarget, minmatch = '', '', 1e99
                    for name in targets:
                        if '%' in name:
                            regex = name.replace('%', '(\w+)', 1)
                            match = re.search(regex, component)
                            if match and len(match.group(1)) < minmatch:
                                wildcard = match.group(1)
                                minmatch = len(wildcard)
                                matchtarget = name
                    resolved = component.replace('%', wildcard, 1)
                    targets[resolved] = targets[matchtarget]
                    targets[resolved]['pm_target_match'] = wildcard
                    wildcards.add(matchtarget)

        # Scan through the top-level targets with wildcards and remove them
        # since the last block resolved all components into top-level static targets.
        for name in wildcards:
            del targets[name]

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

        # Building the dependency list is really simple because our
        # buildtarget is the sink of a graph we want to perform DFS on.
        schedule = []
        def visit(name):
            target = targets[name]
            if not target['visited']:
                target['visited'] = True
                for component in target['components']:
                    visit(component)
                schedule.append(name)

        visit(buildtarget)

        def submit(name, lastid=None):
            target = targets[name]
            subenv = target['env'].asdict()
            with tempfile.NamedTemporaryFile() as taskfile:
                taskfile.write('\n'.join(cmd for cmd in target['cmds']))
                taskfile.flush()
                if self.local:
                    lastid = 'local'
                    cmd = ('/bin/bash', taskfile.name)
                    out, err = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=subenv).communicate()
                    return out.rstrip()
                else:
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
