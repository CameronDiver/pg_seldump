#!/usr/bin/env python3
"""
Writing to a dump file.

This file is part of pg_seldump.
"""

import sys
import math
import logging
from datetime import datetime

import psycopg2
from psycopg2 import sql

from .consts import PROJECT_URL, VERSION
from .writer import Writer
from .exceptions import DumpError

logger = logging.getLogger("seldump.dumpwriter")


class DumpWriter(Writer):
    def __init__(self, outfile, reader):
        self.outfile = outfile
        self.reader = reader

        self._start_time = None
        self._copy_start_pos = None
        self._copy_size = None

    def close(self):
        if self.outfile is not sys.stdout:
            self.outfile.close()

    def dump_table(self, table, action):
        logger.info("writing %s %s", table.kind, table.escaped)

        self._begin_table(table)
        self._copy_table(table, action)
        self._end_table(table)

    def _begin_table(self, table):
        self.write("\nalter table %s disable trigger all;\n" % table.escaped)

    def _end_table(self, table):
        self.write("\nalter table %s enable trigger all;\n\n" % table.escaped)

        if self._copy_size is not None:
            if self._copy_size >= 1024:
                pretty = " (%s)" % pretty_size(self._copy_size)
            else:
                pretty = ""

            self.write(
                "-- %s bytes written for table %s%s\n\n"
                % (self._copy_size, table.escaped, pretty)
            )

    def _copy_table(self, table, action):
        assert action.import_statement
        assert action.copy_statement

        self.write(action.import_statement)

        logger.debug("exporting using: %s", action.copy_statement)
        self._begin_copy()
        try:
            self.reader.copy(action.copy_statement, self.outfile)
        except psycopg2.DatabaseError as e:
            raise DumpError(
                "failed to copy from table %s: %s" % (table.escaped, e)
            )
        else:
            self.write("\\.\n")

        self._end_copy()

    def dump_sequence(self, seq, action):
        logger.info("writing %s %s", seq.kind, seq)

        # Escape the sequence as identifier then as string to make a value
        # good for regclass
        name = sql.Identifier(seq.schema, seq.name)
        name = sql.Literal(self.reader.obj_as_string(name))

        val = sql.Literal(self.reader.get_sequence_value(seq))
        stmt = sql.SQL("\nselect pg_catalog.setval({}, {}, true);\n\n").format(
            name, val
        )
        self.write(stmt)

    def dump_materialized_view(self, matview, action):
        logger.info("writing %s %s", matview.kind, matview.escaped)

        assert action.import_statement
        self.write(action.import_statement)

    def begin_dump(self):
        self.write(
            "-- PostgreSQL data dump generated by pg_seldump %s\n" % VERSION
        )
        self.write("-- %s\n\n" % PROJECT_URL)

        self._start_time = now = datetime.utcnow()
        self.write("-- Data dump started at %sZ\n\n" % now)

        self.write("set session authorization default;\n")

    def end_dump(self):
        self.write("\n\nanalyze;\n\n")

        now = datetime.utcnow()
        elapsed = pretty_timedelta(now - self._start_time)
        self.write("-- Data dump finished at %sZ (%s)\n\n" % (now, elapsed))

        # No highlight please
        self.write("-- vim: set filetype=:\n")

    def write(self, data):
        if isinstance(data, sql.Composable):
            data = self.reader.obj_as_string(data)

        self.outfile.write(data)

    def _begin_copy(self):
        """
        Mark the start of the copy of a table data.

        Memorize where we are in the file output file, if the file is seekable.
        """
        if self.outfile.seekable():
            self._copy_start_pos = self.outfile.tell()

    def _end_copy(self):
        """
        Mark the end of the copy of a table data.

        If the file is seekable return the amout of bytes copied.
        """
        if self.outfile.seekable() and self._copy_start_pos is not None:
            self._copy_size = self.outfile.tell() - self._copy_start_pos
            self._copy_start_pos = None


def pretty_size(size):
    """
    Display a size in bytes in a human friendly way
    """
    if size <= 0:
        # Not bothering with negative numbers
        return "%sB" % size

    suffixes = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB")
    i = int(math.floor(math.log(size, 1024)))
    p = math.pow(1024, i)
    s = round(size / p, 2)
    return "%s %s" % (s, suffixes[i])


def pretty_timedelta(delta):
    """
    Display a time interval in a human friendly way
    """
    rem, secs = divmod(abs(delta.total_seconds()), 60)
    rem, mins = divmod(rem, 60)
    days, hours = divmod(rem, 24)
    parts = [(days, "d"), (hours, "h"), (mins, "m"), (secs, "s")]
    while parts and parts[0][0] == 0:
        del parts[0]
    sign = "-" if delta.total_seconds() < 0 else ""
    return sign + " ".join("%.0f%s" % p for p in parts)
