"""
Email handler for request emails
"""

# pylint: disable-msg=E0611

from django.core.files import File
from django.template.loader import render_to_string

import logging
from config.settings import relay
from lamson.routing import route, stateless

import os
from datetime import datetime
from email.utils import parseaddr, getaddresses
from tempfile import NamedTemporaryFile

from foia.models import FOIARequest, FOIADocument, FOIACommunication, FOIAFile
from muckrock.foia.tasks import upload_document_cloud

DOC_CLOUD_TYPES = ['application/pdf', 'application/msword']
IGNORE_TYPES = ['application/x-pkcs7-signature']
TEXT_TYPES = ['text/plain']
ALLOWED_TLDS = ['.gov', '.mil', '.state.ma.us', '.state.ny.us']

# pylint: disable-msg=C0103

@route('(address)@(host)')
@stateless
def REQUEST(message, address=None, host=None):
    """Request auto handler"""
    # pylint: disable-msg=E1101

    try:
        # backup all emails we receive here
        relay.deliver(message, To='backup@muckrock.com')

        foia = FOIARequest.objects.get(mail_id=address)
        from_email = parseaddr(message['from'])[1]
        # this is a python email message object
        msg = message.to_message()

        if not _allowed_email(from_email, foia):
            logging.warning('Bad sender: %s', message['from'])
            message['subject'] = 'Bad Sender: %s' % message['subject']
            relay.deliver(message, To='requests@muckrock.com')
            return

        communication = ''
        attachments = []
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart':
                # just a container for other parts
                continue
            content_type = part.get_content_type()
            file_name = part.get_filename()
            type_ = _file_type(content_type, file_name)
            if type_ == 'text':
                communication += part.get_payload()
                attachments.append('Add to body text - type: %s name: %s' %
                                   (content_type, file_name))

            elif type_ == 'doc_cloud':
                _upload_doc_cloud(foia, file_name, part, message['from'])
                attachments.append('Add as a doc cloud - type: %s name: %s' %
                                   (content_type, file_name))
            elif type_ == 'file':
                _upload_file(foia, file_name, part, message['from'])
                attachments.append('Add as a file - type: %s name: %s' %
                                   (content_type, file_name))
            elif type_ in ['skip', 'ignore']:
                attachments.append('Skipped - type: %s name: %s' %
                                   (content_type, file_name))

        comm = FOIACommunication.objects.create(
                foia=foia, from_who=message['from'][:255], to_who=foia.user.get_full_name(),
                date=datetime.now(), response=True, full_html=False, communication=communication)

        relay.deliver(message, To='requests@muckrock.com')
        relay.send(From='%s@%s' % (address, host), To='requests@muckrock.com',
                   Subject='[RESPONSE] Freedom of Information Request: %s' % foia.title,
                   Body=render_to_string('foia/admin_request.txt',
                                         {'request': foia, 'attachments': attachments,
                                          'message': message}))

        foia.email = from_email
        foia.other_emails = ','.join(email for name, email
                             in getaddresses(msg.get_all('to', []) + msg.get_all('cc', []))
                             if not email.endswith('muckrock.com'))
        foia.save()
        foia.update(comm.anchor())

        # Use NLTK to try and automatically set updated status

    except FOIARequest.DoesNotExist:
        logging.warning('Invalid address: %s', address)
        message['subject'] = 'Invalid address: %s' % address
        relay.deliver(message, To='requests@muckrock.com')
    except Exception as exc:
        # If anything I haven't accounted for happens, at the very least forward
        # the email to requests so it isn't lost
        message['subject'] = 'Uncaught Exception: %s' % message['subject']
        relay.deliver(message, To='requests@muckrock.com')
        raise exc


# factor commonalities out of these two?

def _upload_file(foia, file_name, part, sender):
    """Upload a file to attach to a FOIA request"""
    # pylint: disable-msg=E1101

    with NamedTemporaryFile() as temp_file:
        temp_file.write(part.get_payload(decode=True))
        foia_file = FOIAFile(foia=foia, date=datetime.now(), source=sender[:70])
        foia_file.ffile.save(file_name, File(temp_file))
        foia_file.save()

def _upload_doc_cloud(foia, file_name, part, sender):
    """Upload a document cloud to attach to a FOIA request"""
    # pylint: disable-msg=E1101

    access = 'private' if foia.is_embargo() else 'public'
    source = foia.agency.name if foia.agency else sender

    with NamedTemporaryFile() as temp_file:
        temp_file.write(part.get_payload(decode=True))
        foia_doc = FOIADocument(foia=foia, title=os.path.splitext(file_name)[0],
                                source=source, access=access, date=datetime.now())
        foia_doc.document.save(file_name, File(temp_file))
        foia_doc.save()
        upload_document_cloud.apply_async(args=[foia_doc.pk, False], countdown=3)

def _allowed_email(email, foia):
    """Is this an allowed email?"""

    if foia.email and '@' in foia.email and email.endswith(foia.email.split('@')[1]):
        return True
    if foia.agency and email in foia.agency.get_other_emails():
        return True
    return any(email.endswith(tld) for tld in ALLOWED_TLDS)

def _file_type(content_type, file_name):
    """Determine the attachment's file type"""

    doc_cloud_types = [
        ('application/pdf', 'pdf'),
        ('application/msword', 'doc'),
        ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx'),
        ]
    ignore_types = [('application/x-pkcs7-signature', 'p7s')]

    if content_type == 'text/plain':
        return 'text'
    elif not file_name:
        return 'skip'
    elif any(content_type == itt or file_name.endswith(ite) for itt, ite in ignore_types):
        return 'ignore'
    elif any(content_type == dtt or file_name.endswith(dte) for dtt, dte in doc_cloud_types):
        return 'doc_cloud'
    else:
        return 'file'
