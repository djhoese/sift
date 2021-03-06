#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
matrix.py
=========

PURPOSE
DataMatrix is products X timesteps matrix
Each matrix cell has a state
Some states have UUIDs and therefore data
Search directories and create index of what data is where
Used by Workspace to respond to adjacency queries / product matrix requests

USAGE

dm = DataAdjacencyMatrix('/data', recurse=True)
# search through files
for _ in dm.finditer():
    pass
ds = dm.ix['myproduct', 0]
if ds.state!=state.CACHED:
    for _ in ds.loaditer(my_workspace):
        pass
uuid = ds.uuid



REFERENCES


REQUIRES


:author: R.K.Garcia <rayg@ssec.wisc.edu>
:copyright: 2016 by University of Wisconsin Regents, see AUTHORS for more details
:license: GPLv3, see LICENSE for more details
"""
__author__ = 'rayg'
__docformat__ = 'reStructuredText'

import os, sys
import logging, unittest, argparse
from datetime import timedelta, datetime
from collections import namedtuple
from enum import Enum
from PyQt4.QtCore import QObject, pyqtSignal

LOG = logging.getLogger(__name__)


class state(Enum):
    """
    Products have several states
    UNKNOWN = 0 - undefined
    UNAVAILABLE - implied existence but unknown location
    AVAILABLE - known location but not imported
    CACHED - available in workspace but not converted to layer or layers; has a UUID
    ACTIVE - available as layers, and being presented
    """
    UNKNOWN = 0  # undefined
    UNAVAILABLE = 1  # implied existence, but unknown location
    AVAILABLE = 2  # known location, but hasn't been imported to workspace
    CACHED = 3  # in cache, has UUID, but not being accessed (does not exist as a layer or scene graph element)
    ACTIVE = 4  # available as layer, dataset in workspace, and has at least one presentation

column_info = namedtuple('column_info', ('time', 'product_count'))

row_info = namedtuple('row_info', ('product_name', 'timestep_count'))

product_info = namedtuple('product_info', ('product_name', 'time', 'state', 'path', 'variable', 'slice'))


class DataAdjacencyMatrix(QObject):
    """
    A product x time matrix of available data.
    - directs the workspace to load or unload data
    - transitions data on demand into layers
    - manages default presentation on a per-product basis
    - allows re-ordering of products, resulting in z-order scenegraph changes
    """
    def __init__(self, initial_search_paths=[]):
        super(DataAdjacencyMatrix, self).__init__()

    def add_search_paths(self, *paths):
        pass

    def remove_search_paths(self, *paths):
        pass

    @property
    def column_time_epsilon(self):
        """
        :return: timedelta that determines whether two or more columns are actually from the same time or not
        """
        return timedelta(0)

    @column_time_epsilon.setter
    def _(self, td):
        self._time_epsilon = td
        self._rebuild()

    @property
    def shape(self):
        """
        :return: tuple of (products, timesteps)
        """
        return (0,0)

    def column_info(self, column):
        """
        :param column: 0..n-1 column to get summary information on
        :return: column_info namedtuple
        """

    def row_info(self, row):
        """
        :param column: 0..n-1 column to get summary information on
        :return: column_info namedtuple
        """

    def _rebuild(self, do_signal=True):
        """
        rebuild rows and columns after insertion, combination or deletion
        :param do_signal: whether or not to propagate a Qt refresh signal
        :return: True if dimensionality changed
        """


class tests(unittest.TestCase):
    data_file = os.environ.get('TEST_DATA', os.path.expanduser("~/Data/test_files/thing.dat"))

    def setUp(self):
        pass

    def test_something(self):
        pass


def _debug(type, value, tb):
    "enable with sys.excepthook = debug"
    if not sys.stdin.isatty():
        sys.__excepthook__(type, value, tb)
    else:
        import traceback, pdb
        traceback.print_exception(type, value, tb)
        # …then start the debugger in post-mortem mode.
        pdb.post_mortem(tb)  # more “modern”


def main():
    parser = argparse.ArgumentParser(
        description="PURPOSE",
        epilog="",
        fromfile_prefix_chars='@')
    parser.add_argument('-v', '--verbose', dest='verbosity', action="count", default=0,
                        help='each occurrence increases verbosity 1 level through ERROR-WARNING-INFO-DEBUG')
    parser.add_argument('-d', '--debug', dest='debug', action='store_true',
                        help="enable interactive PDB debugger on exception")
    # http://docs.python.org/2.7/library/argparse.html#nargs
    # parser.add_argument('--stuff', nargs='5', dest='my_stuff',
    #                    help="one or more random things")
    parser.add_argument('inputs', nargs='*',
                        help="input files to process")
    args = parser.parse_args()

    levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    logging.basicConfig(level=levels[min(3, args.verbosity)])

    if args.debug:
        sys.excepthook = _debug

    if not args.inputs:
        unittest.main()
        return 0

    for pn in args.inputs:
        pass

    return 0


if __name__ == '__main__':
    sys.exit(main())
