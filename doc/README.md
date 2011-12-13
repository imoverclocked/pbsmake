# pbsmake documentation
### Documentation describing how to use pbsmake

## Makefile Examples

### Makefile.sleep-job

All lines under a target are sent verbatim as a script (minus the tabs) to
be run under the job scheduler. The usual PBS script conventions apply.

#### Makefile

    default:
    	#PBS -k oe
    	#PBS -j oe
    	#PBS -l mem=10mb
    	#PBS -l walltime=00:01:10
    	#PBS -V
    	sleep 60

#### Generated script/job

    #PBS -k oe
    #PBS -j oe
    #PBS -l mem=10mb
    #PBS -l walltime=00:01:10
    #PBS -V
    sleep 60

### Makefile.interpolated

There is one exception to the "verbatim" rule above: #PBS lines are
interpolated before being written out. This allows attributes to be easily
set for each job.

#### Makefile

    JOB_NAME=sleep_job
    CPUTIME=00:01:10
    SLEEP_DELAY=60

    default:
    	#PBS -k oe
    	#PBS -j oe
    	#PBS -l mem=10mb
    	#PBS -l walltime=${WALLTIME}
    	#PBS -N ${JOB_NAME}
    	#PBS -V
    	sleep $SLEEP_DELAY

#### Generated script/job

    #PBS -k oe
    #PBS -j oe
    #PBS -l mem=10mb
    #PBS -l walltime=00:01:10
    #PBS -N sleep_job
    #PBS -V
    sleep $SLEEP_DELAY

NB: the sleep variable is not interpolated until the job executes.

### Makefile.python-script

Since lines are sent verbatim and run as scripts, other shell interpreters are
available as usual from the PBS environment.

#### Makefile

    default:
    	#!/usr/bin/python
    	#PBS -k oe
    	#PBS -j oe
    	#PBS -l mem=1gb
    	#PBS -l walltime=00:01:00
    	#PBS -V
    	#PBS -S /bin/sh
    	for w in "python is pretty neat".split():
    	    print w

#### Generated script/job

    #!/usr/bin/python
    #PBS -k oe
    #PBS -j oe
    #PBS -l mem=1gb
    #PBS -l walltime=00:01:00
    #PBS -V
    #PBS -S /bin/sh
    for w in "python is pretty neat".split():
        print w


## Script-makefile examples

By setting pbsmake to be the script parser for a file, you can create simple
commands with default targets that will submit jobs to your cluster. Here are
some examples.

### script.basic

Here is a basic script that will submit a job that will run echo/data/echo as
you would expect.

#### scriptfile

    #!/path/to/pbsmake -f

    STR="howdy pardner"

    default:
    	#!/usr/bin/bash
    	#PBS -k oe -j oe -l mem=1gb,walltime=00:01:00 -V -S /bin/sh
    	echo -n Running default target at
    	date
    	echo $STR

#### Usage

    $ ./script.basic

### script.advanced

Here is a script that allows pattern matching based rules to create arbitrary
.txt files from a corresponding .raw file.

#### scriptfile

    #!/path/to/pbsmake -f

    %.txt:
    	#!/usr/bin/bash
    	#PBS -k oe -j oe -l mem=1gb,walltime=00:01:00 -V -S /bin/sh
    	DATE=$(date)
    	sed "s/DATE/$DATE/" < ${pm_target_match}.raw > ${pm_target}

#### Usage

    $ echo file last updated on DATE > a.raw
    $ ./scriptfile a.txt
    ... wait for job to complete ...
    $ cat a.txt
    file last updated on Tue Dec 13 14:27:53 MST 2011

#### Notes

You'll notice that $pm_target_match is introduced here and is used similarly to
the classic make match variable. The reason for the change is that the script
is interpreted by languages other than make by default. Accessing an
environment variable called $@ (for example) may not be trivial in all
languages thus human readable names were chosen to replace them.

