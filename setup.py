#!/usr/bin/env python

from distutils.core import setup

setup(
	name='pbsmake',
	description='Implement job submission and dependencies using a makefile-like syntax',
	long_description="""
	This script allows users to create job definitions in a makefile-like
	syntax. This helps keep job dependencies clear or for the simple case
	it can help to submit many similar jobs that run slightly differently
	depending solely on their job name. Invoking a script with pbsmake -f
	allows wildcard matching and generic commands to be created.
	EG: create a pbsmake script called "compress_dir" and call it via:
	% compress_dir *
	everything that matches * will be created as its own job and suitably
	compressed.
	""",
	version="0.1.6",
	scripts=["pbsmake"],
	requires=['pbs (>=0.95)']
	)

