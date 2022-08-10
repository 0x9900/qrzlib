#!/usr/bin/env python3
# Fred (W6BSD) 2022
#
import sys

from setuptools import setup

import qrzlib

__author__ = "Fred C. (W6BSD)"
__version__ = qrzlib.__version__
__license__ = 'BSD'

py_version = sys.version_info[:2]
if py_version < (3, 5):
  raise RuntimeError('qrzlib requires Python 3.5 or later')

def readme():
  with open('README.md', encoding="utf-8") as fdr:
    return fdr.read()

setup(
  name='qrzlib',
  version=__version__,
  description='Random Length Antenna Calculator',
  long_description=readme(),
  long_description_content_type='text/markdown',
  url='https://github.com/0x9900/qrzlib/',
  license=__license__,
  author=__author__,
  author_email='w6bsd@bsdworld.org',
  py_modules=['qrzlib'],
  entry_points = {
    'console_scripts': ['qrzlib = qrzlib:main'],
  },
  classifiers=[
    'Development Status :: 3 - Alpha',
    'Intended Audience :: Telecommunications Industry',
    'License :: OSI Approved :: BSD License',
    'Operating System :: OS Independent',
    'Programming Language :: Python',
    'Programming Language :: Python :: 3',
    'Topic :: Communications :: Ham Radio',
  ],
)
