# Copyright (C) 2001-2006 by the Free Software Foundation, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

"""Reading and writing message objects and message metadata."""

# enqueue() and dequeue() are not symmetric.  enqueue() takes a Message
# object.  dequeue() returns a email.Message object tree.
#
# Message metadata is represented internally as a Python dictionary.  Keys and
# values must be strings.  When written to a queue directory, the metadata is
# written into an externally represented format, as defined here.  Because
# components of the Mailman system may be written in something other than
# Python, the external interchange format should be chosen based on what those
# other components can read and write.
#
# Most efficient, and recommended if everything is Python, is Python marshal
# format.  Also supported by default is Berkeley db format (using the default
# bsddb module compiled into your Python executable -- usually Berkeley db
# 2), and rfc822 style plain text.  You can write your own if you have other
# needs.

import os
import sha
import time
import email
import errno
import cPickle
import marshal

from Mailman import Message
from Mailman import mm_cfg
from Mailman import Utils

# 20 bytes of all bits set, maximum sha.digest() value
shamax = 0xffffffffffffffffffffffffffffffffffffffffL

# This flag causes messages to be written as pickles (when True) or text files
# (when False).  Pickles are more efficient because the message doesn't need
# to be re-parsed every time it's unqueued, but pickles are not human readable.
SAVE_MSGS_AS_PICKLES = True



class Switchboard:
    def __init__(self, whichq, slice=None, numslices=1):
        self.__whichq = whichq
        # Create the directory if it doesn't yet exist.
        # FIXME
        omask = os.umask(0)                       # rwxrws---
        try:
            try:
                os.mkdir(self.__whichq, 0770)
            except OSError, e:
                if e.errno <> errno.EEXIST: raise
        finally:
            os.umask(omask)
        # Fast track for no slices
        self.__lower = None
        self.__upper = None
        # BAW: test performance and end-cases of this algorithm
        if numslices <> 1:
            self.__lower = ((shamax+1) * slice) / numslices
            self.__upper = (((shamax+1) * (slice+1)) / numslices) - 1

    def whichq(self):
        return self.__whichq

    def enqueue(self, _msg, _metadata={}, **_kws):
        # Calculate the SHA hexdigest of the message to get a unique base
        # filename.  We're also going to use the digest as a hash into the set
        # of parallel qrunner processes.
        data = _metadata.copy()
        data.update(_kws)
        listname = data.get('listname', '--nolist--')
        # Get some data for the input to the sha hash
        now = time.time()
        if SAVE_MSGS_AS_PICKLES and not data.get('_plaintext'):
            protocol = 1
            msgsave = cPickle.dumps(_msg, protocol)
        else:
            protocol = 0
            msgsave = cPickle.dumps(str(_msg), protocol)
        hashfood = msgsave + listname + `now`
        # Encode the current time into the file name for FIFO sorting in
        # files().  The file name consists of two parts separated by a `+':
        # the received time for this message (i.e. when it first showed up on
        # this system) and the sha hex digest.
        #rcvtime = data.setdefault('received_time', now)
        rcvtime = data.setdefault('received_time', now)
        filebase = `rcvtime` + '+' + sha.new(hashfood).hexdigest()
        filename = os.path.join(self.__whichq, filebase + '.pck')
        tmpfile = filename + '.tmp'
        # Always add the metadata schema version number
        data['version'] = mm_cfg.QFILE_SCHEMA_VERSION
        # Filter out volatile entries
        for k in data.keys():
            if k.startswith('_'):
                del data[k]
        # We have to tell the dequeue() method whether to parse the message
        # object or not.
        data['_parsemsg'] = (protocol == 0)
        # Write to the pickle file the message object and metadata.
        omask = os.umask(007)                     # -rw-rw----
        try:
            fp = open(tmpfile, 'w')
            try:
                fp.write(msgsave)
                cPickle.dump(data, fp, protocol)
                fp.flush()
                os.fsync(fp.fileno())
            finally:
                fp.close()
        finally:
            os.umask(omask)
        os.rename(tmpfile, filename)
        return filebase

    def dequeue(self, filebase):
        # Calculate the filename from the given filebase.
        filename = os.path.join(self.__whichq, filebase + '.pck')
        # Read the message object and metadata.
        fp = open(filename)
        os.unlink(filename)
        try:
            msg = cPickle.load(fp)
            data = cPickle.load(fp)
        finally:
            fp.close()
        if data.get('_parsemsg'):
            msg = email.message_from_string(msg, Message.Message)
        return msg, data

    def files(self):
        times = {}
        lower = self.__lower
        upper = self.__upper
        for f in os.listdir(self.__whichq):
            # By ignoring anything that doesn't end in .pck, we ignore
            # tempfiles and avoid a race condition.
            if not f.endswith('.pck'):
                continue
            filebase = os.path.splitext(f)[0]
            when, digest = filebase.split('+')
            # Throw out any files which don't match our bitrange.  BAW: test
            # performance and end-cases of this algorithm.
            if lower is None or (lower <= long(digest, 16) < upper):
                times[float(when)] = filebase
        # FIFO sort
        keys = times.keys()
        keys.sort()
        return [times[k] for k in keys]
