# pbsmake
### distributed make for [PBS][pbs] job scheduling systems

## install

`pbsmake` is a simple Python [script][pbsmake], so you only need to
add it to your `$PATH`. We also rely on the [argparse][argparse] module
which is included in Python 2.7 and [q\* family][qsub] of commands from
a PBS installation in `$PATH`.

## usage

Running `pbsmake` requires a build target to be specified on the command-line
and takes an optional path to a makefile. If no makefile is given, the default
'./Makefile' will be used like in traditional `make` programs.

    $ python pbsmake.py
    usage: pbsmake.py [-h] [-f MAKEFILE] target [target ...]

    $ python pbsmake.py mytarget

    $ python pbsmake.py -f some/Makefile myothertarget

Also, pbsmake can be used as a script interpreter by using -f as follows.

    --- makescript ---
    #!/usr/bin/env pbsmake.py -f

    target1: target2
    	echo running target 1

    target2:
    	echo running target 2
    ------------------

    $ ./makescript target1

Sometimes this may not work (notably OSX) and you will need to use a work-around:
    (NB: still need to thoroughly test this...)

    --- makescript ---
    #!/usr/bin/env python /path/to/pbsmake.py -f

    target1: target2
    	echo running target 1

    target2:
    	echo running target 2
    ------------------

## authors

[Tim Spriggs][tims]

[Chris Van Horne][cwvh]


[pbs]:      http://en.wikipedia.org/wiki/Portable_Batch_System
[pbsmake]:  https://github.com/cwvh/pbsmake/blob/master/pbsmake.py
[argparse]: http://docs.python.org/dev/library/argparse.html
[qsub]:     http://www.clusterresources.com/torquedocs/commands/qsub.shtml
[tims]:     https://github.com/imoverclocked
[cwvh]:     https://github.com/cwvh
