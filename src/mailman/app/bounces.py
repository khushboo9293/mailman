# Copyright (C) 2007-2011 by the Free Software Foundation, Inc.
#
# This file is part of GNU Mailman.
#
# GNU Mailman is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# GNU Mailman is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# GNU Mailman.  If not, see <http://www.gnu.org/licenses/>.

"""Application level bounce handling."""

from __future__ import absolute_import, unicode_literals

__metaclass__ = type
__all__ = [
    'ProbeVERP',
    'StandardVERP',
    'bounce_message',
    'scan_message',
    'send_probe',
    ]


import re
import logging

from email.mime.message import MIMEMessage
from email.mime.text import MIMEText
from email.utils import parseaddr
from string import Template
from zope.component import getUtility
from zope.interface import implements

from mailman.app.finder import find_components
from mailman.config import config
from mailman.core.i18n import _
from mailman.email.message import UserNotification
from mailman.interfaces.bounce import IBounceDetector
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.pending import IPendable, IPendings
from mailman.utilities.email import split_email
from mailman.utilities.i18n import make
from mailman.utilities.string import oneline

log = logging.getLogger('mailman.config')
elog = logging.getLogger('mailman.error')

DOT = '.'



def bounce_message(mlist, msg, e=None):
    """Bounce the message back to the original author.

    :param mlist: The mailing list that the message was posted to.
    :type mlist: `IMailingList`
    :param msg: The original message.
    :type msg: `email.message.Message`
    :param e: Optional exception causing the bounce.
    :type e: Exception
    """
    # Bounce a message back to the sender, with an error message if provided
    # in the exception argument.
    if msg.sender is None:
        # We can't bounce the message if we don't know who it's supposed to go
        # to.
        return
    subject = msg.get('subject', _('(no subject)'))
    subject = oneline(subject, mlist.preferred_language.charset)
    if e is None:
        notice = _('[No bounce details are available]')
    else:
        notice = _(e.notice)
    # Currently we always craft bounces as MIME messages.
    bmsg = UserNotification(msg.sender, mlist.owner_address, subject,
                            lang=mlist.preferred_language)
    # BAW: Be sure you set the type before trying to attach, or you'll get
    # a MultipartConversionError.
    bmsg.set_type('multipart/mixed')
    txt = MIMEText(notice, _charset=mlist.preferred_language.charset)
    bmsg.attach(txt)
    bmsg.attach(MIMEMessage(msg))
    bmsg.send(mlist)



def scan_message(mlist, msg):
    """Scan all the message for heuristically determined bounce addresses.

    :param mlist: The mailing list.
    :type mlist: `IMailingList`
    :param msg: The bounce message to scan.
    :type msg: `Message`
    :return: The set of bouncing addresses found in the scanned message.  The
        set will be empty if no addresses were found.
    :rtype: set
    """
    for detector_class in find_components('mailman.bouncers', IBounceDetector):
        addresses = detector_class().process(msg)
        # Detectors may return None or an empty sequence to signify that no
        # addresses have been found.
        if addresses:
            return set(addresses)
    return set()



class _BaseVERPParser:
    """Base class for parsing VERP messages.

    Sadly not every MTA bounces VERP messages correctly, or consistently.
    First, the To: header is checked, then Delivered-To: (Postfix),
    Envelope-To: (Exim) and Apparently-To:.  Note that there can be multiple
    headers so we need to search them all
    """

    def __init__(self, pattern):
        self._pattern = pattern
        self._cre = re.compile(pattern, re.IGNORECASE)

    def _get_addresses(self, match_object):
        raise NotImplementedError

    def get_verp(self, mlist, msg):
        """Extract a set of VERP bounce addresses.


        :param mlist: The mailing list being checked.
        :type mlist: `IMailingList`
        :param msg: The message being parsed.
        :type msg: `email.message.Message`
        :return: The set of addresses extracted from the VERP headers.
        :rtype: set of strings
        """
        blocal, bdomain = split_email(mlist.bounces_address)
        values = set()
        verp_matches = set()
        for header in ('to', 'delivered-to', 'envelope-to', 'apparently-to'):
            values.update(msg.get_all(header, []))
        for field in values:
            address = parseaddr(field)[1]
            if not address:
                # This header was empty.
                continue
            mo = self._cre.search(address)
            if not mo:
                # This did not match the VERP regexp.
                continue
            try:
                if blocal != mo.group('bounces'):
                    # This was not a bounce to our mailing list.
                    continue
                original_address = self._get_address(mo)
            except IndexError:
                elog.error('Bad VERP pattern: {0}'.format(self._pattern))
                return set()
            else:
                verp_matches.add(original_address)
        return verp_matches



class StandardVERP(_BaseVERPParser):
    def __init__(self):
        super(StandardVERP, self).__init__(config.mta.verp_regexp)

    def _get_address(self, match_object):
        return '{0}@{1}'.format(*match_object.group('local', 'domain'))


class ProbeVERP(_BaseVERPParser):
    def __init__(self):
        super(ProbeVERP, self).__init__(config.mta.verp_probe_regexp)

    def _get_address(self, match_object):
        # Extract the token and get the matching address.
        token = match_object.group('token')
        op, address, bmsg = getUtility(IPendings).confirm(token)
        return address



class _ProbePendable(dict):
    """The pendable dictionary for probe messages."""
    implements(IPendable)


def send_probe(member, msg):
    """Send a VERP probe to the member.

    :param member: The member to send the probe to.  From this object, both
        the user and the mailing list can be determined.
    :type member: IMember
    :param msg: The bouncing message that caused the probe to be sent.
    :type msg:
    :return: The token representing this probe in the pendings database.
    :rtype: string
    """
    mlist = getUtility(IListManager).get(member.mailing_list)
    text = make('probe.txt', mlist, member.preferred_language.code,
        listname=mlist.fqdn_listname,
        address= member.address.email,
        optionsurl=member.options_url,
        owneraddr=mlist.owner_address,
        )
    pendable = _ProbePendable(
        member_id=member.member_id,
        message_id=msg['message-id'],
        )
    token = getUtility(IPendings).add(pendable)
    mailbox, domain_parts = split_email(mlist.bounces_address)
    probe_sender = Template(config.mta.verp_probe_format).safe_substitute(
        bounces=mailbox,
        token=token,
        domain=DOT.join(domain_parts),
        )
    # Calculate the Subject header, in the member's preferred language.
    with _.using(member.preferred_language.code):
        subject = _('$mlist.real_name mailing list probe message')
    # Craft the probe message.  This will be a multipart where the first part
    # is the probe text and the second part is the message that caused this
    # probe to be sent.
    probe = UserNotification(member.address.email, probe_sender,
                             subject, lang=member.preferred_language)
    probe.set_type('multipart/mixed')
    notice = MIMEText(text, _charset=mlist.preferred_language.charset)
    probe.attach(notice)
    probe.attach(MIMEMessage(msg))
    probe.send(mlist, envsender=probe_sender, verp=False, probe_token=token)
    return token
