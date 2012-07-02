#!/usr/bin/env python

import argparse
import collections
import copy
import itertools
import os
import pbs
import re
import shlex
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
        return self.parent.setdefault(key, value)

    def interp(self, string, regex=r'(?<!\\)\${[a-zA-Z_][a-zA-Z_0-9]*}', defer=True):
        match = re.search(regex, string)
        while match:
            start, end = match.span()
            var = string[start + 2:end - 1]
            if defer and var == 'pm_target_match':
                continue
            string = ''.join((string[:start], self[var], string[end:]))
            match = re.search(regex, string[end:])
        return string


class Makefile(object):
    def __init__(self):
        self.targets = collections.defaultdict(list)
        self.default = ''
        # Construct self.attrs from available attributes in the pbs module
        # this provides a mapping from human readable names (no spaces) to
        # the module ATTR_* names. Not all ATTR_ entities are interesting.
        self.attrs = {}
        pbs_module_attrs = [a for a in dir(pbs) if a[0:5] == 'ATTR_']
        for attr in pbs_module_attrs:
            self.attrs[ getattr(pbs, attr) ] = str

        srvname = pbs.pbs_default()
        self.conn = pbs.pbs_connect(srvname)

        # By default, submit jobs to pbs
        self.pbs(True)
        self.dotAliases = {}

    def dot(self, dot_output=False):
        if dot_output:
            self.submit_target = self.submit_dot
        return self.submit_target == self.submit_dot

    def local(self, run_locally=False):
        if run_locally:
            self.submit_target = self.submit_local
        return self.submit_target == self.submit_local

    def pbs(self, run_pbs=False):
        if run_pbs:
            self.submit_target = self.submit_pbs
        return self.submit_target == self.submit_pbs

    @staticmethod
    def canonicalize(name):
        return re.sub('::afterok', '', name)

    def addtarget(self, name, components=None, cmds=None, attrs=None):
        name = self.canonicalize(name)
        if not self.default and '%' not in name:
            self.default = name
        self.current = name
        self.targets[name] = collections.defaultdict(list)
        self.addcomponents(name, components)
        self.addattrs(name, attrs)
        self.addcmds(name, cmds)

    def addattrs(self, name, attrs):
        if isinstance(attrs, basestring):
            attrs = dict([attrs.split(" ", 2)])
        attrs = attrs or {}
        self.targets[name].setdefault('attrs', {})
        self.targets[name]['attrs'].update(attrs)
        # Validate attribute names
        unknowns = [ dne for dne in attrs.keys() if dne not in self.attrs.keys() ]
        if len(unknowns):
            raise Exception("Unknown pbs attribute(s): " + " ".join(unknowns))

    def addcmds(self, name, cmds):
        if isinstance(cmds, basestring):
            cmds = [cmds]
        self.targets[name]['cmds'] += cmds or []

    def addcomponents(self, name, components):
        if isinstance(components, basestring):
            components = [components]
        components = map(self.canonicalize, components or [])
        self.targets[name]['components'] += components

    def resolve(self, resolvetarget):
        ''' take the list of targets and look for something that matches a given
        name, optionally making a target from a wildcarded target. Also, resolve components. '''
        targets = self.targets
        if resolvetarget not in targets:
            matched_name = None
            wildcard, minmatch = '', 1e99
            for name in [tgt for tgt in targets if '%' in tgt]:
                regex = name.replace("%", '(\S+)', 1) + "$"
                match = re.match(regex, resolvetarget)
                if match and len(match.group(1)) < minmatch:
                    wildcard = match.group(1)
                    matched_name = name
                    minmatch = len(wildcard)

            if not matched_name:
                raise Exception('Could not resolve target: %s' % resolvetarget)

            target = targets[resolvetarget] = copy.deepcopy( targets[matched_name] )
            target['pm_target_match'] = wildcard

            def replace_perc(tgt_str):
                return tgt_str.replace('%', wildcard, 1)

            target['components'] = map(replace_perc, target['components'])

        target = targets[resolvetarget]
        return target

    def build(self, buildtargets):
        self.start()
        if isinstance(buildtargets, basestring):
            buildtargets = [buildtargets]
        if len(buildtargets) == 0:
            buildtargets = [self.default]
        buildtargets = map(self.canonicalize, buildtargets)
        targets = self.targets

        # find all targets stemming from buildtarget and make sure they resolve
        unresolved = []
        unresolved.extend( buildtargets )
        resolved = []
        while unresolved:
            tgt = unresolved.pop(0)
            res = self.resolve(tgt)
            components = res['components']
            unresolved.extend(res['components'])
            resolved.append(tgt)
            # Make sure we don't re-iterate over resolved objects 
            map(unresolved.remove, [r for r in resolved if r in unresolved])

        # remove all targets that are not in the resolve list
        #   (removes all wildcard targets too)
        for del_target in [tgt for tgt in targets if tgt not in resolved]:
            del(targets[del_target])

        # Traverse all top-level targets and attempt to resolve their wildcard
        # dependencies. This must be done because the toplogical sort requires
        # that there be no wildcard targets. An optimization could be made to
        # embed the topological sort in this step and minimize the amount of
        # traversals. Currently, the entire makefile must be resolved before
        # sorting, regardless if the buildtarget has no components.
        # -- This logic has been kept in-tact with the above two blocks

        # setup useful environment variables
        cwd = os.getcwd()
        for name in targets:
            subenv = self.env.deepcopy()
            subenv['pm_target_name'] = name
            subenv.setdefault('PBS_O_WORKDIR', cwd)
            targets[name]['env'] = subenv
            if 'pm_target_match' in targets[name]:
                subenv['pm_target_match'] = targets[name]['pm_target_match']

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

        for buildtarget in buildtargets:
            visit(buildtarget)

        for name in schedule:
            print self.submit(name)

        for name in targets.iterkeys():
            if '::' in name and name not in schedule:
                parent = re.sub('::.+', '', target)
                if parent in schedule:
                    torqueid = targets[parent]['torqueid']
                    print self.submit(name, torqueid)

        self.finish()

    def start(self):
        if self.dot():
            print "digraph pbsmakefile {"

    def finish(self):
        if self.dot():
            print "\n".join(self.dotaliases())
            print "}"

    def getdotalias(self, name):
        aliases = self.dotAliases
        if name not in aliases:
            aliases[name] = "t_%d" % len(aliases.keys())
        return aliases[name]

    def submit_dot(self, name, taskfile, lastid=None):
        target = self.targets[name]

        for dep in target['components']:
            print "%s -> %s;" % ( self.getdotalias(name), self.getdotalias(dep) )

    def dotaliases(self):
        ''' return a list of alias [label="name"] lines for dot '''
        ret = []
        for name,alias in self.dotAliases.items():
            ret.append( '%s [label="%s"];' % (alias, name) )
        return ret

    def submit_pbs(self, name, taskfile, lastid=None):
        targets = self.targets
        target = targets[name]
        subenv = target['env'].asdict()

        target['attrs'].setdefault(pbs.ATTR_N, name)

        # Just include all variables by default
        varlist = ','.join('%s=%s' % (k,v) for k,v in subenv.iteritems())
        target['attrs'].setdefault(pbs.ATTR_v, varlist)

        # Track job dependencies
        dependencies = []
        dep_type = name.partition('::')[-1] or 'afterok'
        for dep in target['components']:
            dependencies.append("%s:%s" % (dep_type, targets[dep]['torqueid']))
        if lastid:
            dependencies.append("%s:%s" % (dep_type, lastid))
        if dependencies:
            target['attrs'][pbs.ATTR_depend] = ",".join(dependencies)

        # /bin/sh as a default shell will generally do the right thing.
        # It honors #! syntax at the beginning of the file and it
        # interprets basic commands without a #! at the beginning of
        # the file. Obscure users can opt for other shells
        # (eg: bash,csh,ksh,python,...) via the standard #! syntax
        #   -- This default ensures users with non-standard shells
        #      can still use pbsmake files from other users.
        target['attrs'].setdefault(pbs.ATTR_S, '/bin/sh')

        # We need to handle ATTR_l specially. Each resource needs its own
        # attropl with the name pbs.ATTR_l:
        attr_l = []
        if pbs.ATTR_l in target['attrs']:
            attr_l = target['attrs'][pbs.ATTR_l].split(",")
            del(target['attrs'][pbs.ATTR_l])

        # Attach attributes to job as the pbs module expects it
        attropl = pbs.new_attropl(len(target['attrs']) + len(attr_l))
        i=0
        for n in target['attrs']:
            attropl[i].name = n
            attropl[i].value = target['env'].interp(target['attrs'][n], defer=False)
            i += 1
        for n in attr_l:
            attropl[i].name = pbs.ATTR_l
            res, val = n.split("=",1)
            attropl[i].resource = res
            attropl[i].value = target['env'].interp(val, defer=False)
            i += 1
        try:
            destination = target['attrs']['queue']
        except KeyError:
            destination = ''

        # attempt to submit job
        lastid = pbs.pbs_submit(self.conn, attropl, taskfile.name, destination, '')
        if lastid:
            target['torqueid'] = lastid
        else:
            print "Error submitting job: %s\n\tAttributes:" % name
            for attr,val in target['attrs'].items():
                print "\t\t%s: %s" % ( attr, val )
            raise Exception(pbs.error())

        return lastid

    def submit_local(self, name, taskfile, lastid=None):
        lastid = 'local'
        cmd = ('/bin/bash', taskfile.name)
        out, err = subprocess.Popen(cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=subenv).communicate()
        print out
        return lastid

    def submit(self, name, lastid=None):
        targets = self.targets
        target = targets[name]
        subenv = target['env'].asdict()
        with tempfile.NamedTemporaryFile() as taskfile:
            taskfile.write('\n'.join(cmd for cmd in target['cmds']))
            taskfile.flush()
            lastid = self.submit_target(name, taskfile, lastid)

        if lastid:
            return '%s(%s) scheduled' % (name, lastid)
        return ''


def parse(iterable, env=Env()):
    # OrderedDict so that the patterns are applied in the order they are listed
    # below. Other orders are not well tested and may blow up.
    handlers = collections.OrderedDict()
    class pattern(object):
        def __init__(self, regex):
            self.regex = regex
        def __call__(self, function):
            if self.regex in handlers:
                del handlers[self.regex]
            handlers[self.regex] = function
            def wrap(*args, **kwds):
                function(*args, **kwds)
            return wrap

    def execshellcmds(s, regex=r'\$\((.+)\)'):
        "Exec any $(..) shell commands found in the string s."
        match = re.search(regex, s)
        while match:
            start, end = match.span()
            cmd = s[start + 2:end - 1]
            result, error = subprocess.Popen(shlex.split(cmd),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
            if error:
                error = error.rstrip()
                print >>sys.stderr, "sub-shell '%s' failed with '%s'" % (cmd, error)
                sys.exit(1)
            else:
                s = ''.join((s[:start], result.rstrip(), s[end:]))
                match = re.search(regex, s[end:])
        return s

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9]*)\s*=\s*(?:[\"\'])?(.+?)(?:[\"\'])?$')
    def vardecl(match, env=env):
        name, value = match.groups()
        env[name] = execshellcmds(env.interp(str(value)))
        return name + '=' + value

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9]*)\s*\+=\s*(?:[\"\'])?(.+?)(?:[\"\'])?$')
    def varapdecl(match, env=env):
        name, value = match.groups()
        env[name] += execshellcmds(env.interp(str(value)))
        return name + '+=' + value

    @pattern(r'^([a-zA-Z_\$\%][a-zA-Z_0-9]*)\s*\?=\s*(?:[\"\'])?(.+?)(?:[\"\'])?$')
    def varcondecl(match, env=env):
        name, value = match.groups()
        env.setdefault(name, execshellcmds(env.interp(str(value))))
        return name + '?=' + value

    @pattern(r'^(\S+)\s*:(?::(\S+):)?\s*(.*)$')
    def target(match, env=env):
        labels = ('name', 'dep', 'components')
        groups = dict(itertools.izip(labels, match.groups()))
        env.env.update(dict(pm_target_name=groups['name']))
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

    @pattern(r'^\t@(\w+)\s*(.*)$')
    def attribute(match, env=env):
        attr = match.group(1)
        val = match.group(2)
        makefile.addattrs(makefile.current, {attr: val})
        return '\t@' + attr + " " + val

    @pattern(r'^\t(.+)$')
    def command(match, env=env):
        cmd = match.group(1)
        makefile.addcmds(makefile.current, cmd)
        return '\t' + cmd

    makefile = Makefile()
    for line in iterable:
        result = None
        for pattern, function in handlers.items():
            match = re.match(pattern, line)
            if match is not None:
                result = function(match)
                # print '%-10s: %s' % (handlers[pattern].__name__, result)
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
    parser.add_argument('-d', '--dot', default=False, action='store_true')
    parser.add_argument('--attrs', default=False, action='store_true')
    args = parser.parse_args()

    if args.attrs:
        for attr in [n for n in dir(pbs) if n[0:5] == 'ATTR_']:
            print "%25s: %s" % ( getattr(pbs, attr), attr )
        sys.exit(0)

    with open(args.makefile) as f:
        contents = (line.rstrip() for line in f.readlines() if line.strip())
        makefile = parse(contents)
        makefile.local(args.local)
        makefile.dot(args.dot)
        if not args.target and makefile.default:
            args.target = [makefile.default]
        makefile.build(args.target)

# vim: ts=4 sts=4 sw=4 et :
