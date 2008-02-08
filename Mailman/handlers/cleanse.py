# Copyright (C) 1998-2008 by the Free Software Foundation, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301,
# USA.

"""Cleanse certain headers from all messages."""

import logging

from email.Utils import formataddr

from Mailman.handlers.cook_headers import uheader

log = logging.getLogger('mailman.post')



def process(mlist, msg, msgdata):
    # Always remove this header from any outgoing messages.  Be sure to do
    # this after the information on the header is actually used, but before a
    # permanent record of the header is saved.
    del msg['approved']
    # Remove this one too.
    del msg['approve']
    # Also remove this header since it can contain a password
    del msg['urgent']
    # We remove other headers from anonymous lists
    if mlist.anonymous_list:
        log.info('post to %s from %s anonymized',
                 mlist.fqdn_listname, msg.get('from'))
        del msg['from']
        del msg['reply-to']
        del msg['sender']
        # Hotmail sets this one
        del msg['x-originating-email']
        i18ndesc = str(uheader(mlist, mlist.description, 'From'))
        msg['From'] = formataddr((i18ndesc, mlist.posting_address))
        msg['Reply-To'] = mlist.posting_address
    # Some headers can be used to fish for membership
    del msg['return-receipt-to']
    del msg['disposition-notification-to']
    del msg['x-confirm-reading-to']
    # Pegasus mail uses this one... sigh
    del msg['x-pmrqc']
    # Don't let this header be spoofed.  See RFC 5064.
    del msg['archived-at']