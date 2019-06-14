"""
Celery tasks for the task application
"""

# Django
from celery.schedules import crontab
from celery.task import periodic_task, task
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.utils import timezone

# Standard Library
from cStringIO import StringIO
from random import randint

# Third Party
from boto.s3.connection import S3Connection
from boto.s3.key import Key
from fpdf import FPDF
from PyPDF2 import PdfFileMerger, PdfFileReader
from PyPDF2.utils import PdfReadError
from requests.exceptions import RequestException

# MuckRock
from muckrock.communication.models import MailCommunication
from muckrock.foia.models import FOIACommunication, FOIARequest
from muckrock.task.filters import SnailMailTaskFilterSet
from muckrock.task.models import FlaggedTask, SnailMailTask
from muckrock.task.pdf import CoverPDF, SnailMailPDF


@task(ignore_result=True, name='muckrock.task.tasks.submit_review_update')
def submit_review_update(foia_pks, reply_text, **kwargs):
    """Submit all the follow ups after updating agency contact information"""
    # pylint: disable=unused-argument
    foias = FOIARequest.objects.filter(pk__in=foia_pks)
    muckrock_staff = User.objects.get(username='MuckrockStaff')
    for foia in foias:
        FOIACommunication.objects.create(
            foia=foia,
            from_user=muckrock_staff,
            to_user=foia.get_to_user(),
            datetime=timezone.now(),
            response=False,
            communication=reply_text,
        )
        foia.submit(switch=True)


@task(
    ignore_result=True,
    time_limit=900,
    name='muckrock.task.tasks.snail_mail_bulk_pdf_task',
)
def snail_mail_bulk_pdf_task(pdf_name, get, **kwargs):
    """Save a PDF file for all open snail mail tasks"""
    # pylint: disable=too-many-locals
    # pylint: disable=unused-argument
    # pylint: disable=too-many-statements
    cover_info = []
    bulk_merger = PdfFileMerger(strict=False)

    snails = SnailMailTaskFilterSet(
        get,
        queryset=SnailMailTask.objects.filter(resolved=False).order_by(
            '-amount',
            'communication__foia__agency',
        ).preload_pdf(),
    ).qs[:100]

    blank_pdf = FPDF()
    blank_pdf.add_page()
    blank = StringIO(blank_pdf.output(dest='S'))
    for snail in snails.iterator():
        # generate the pdf and merge all pdf attachments
        pdf = SnailMailPDF(snail.communication, snail.category, snail.amount)
        pdf.generate()
        single_merger = PdfFileMerger(strict=False)
        single_merger.append(StringIO(pdf.output(dest='S')))
        files = []
        for file_ in snail.communication.files.all():
            if file_.get_extension() == 'pdf':
                try:
                    pages = PdfFileReader(file_.ffile).getNumPages()
                    single_merger.append(file_.ffile)
                    files.append((file_, 'attached', pages))
                except (PdfReadError, ValueError):
                    files.append((file_, 'error', 0))
            else:
                files.append((file_, 'skipped', 0))
        single_pdf = StringIO()
        try:
            single_merger.write(single_pdf)
        except PdfReadError:
            cover_info.append((snail, None, files))
            continue
        else:
            cover_info.append((snail, pdf.page, files))

        # attach to the mail communication
        mail, _ = MailCommunication.objects.update_or_create(
            communication=snail.communication,
            defaults={
                'to_address': snail.communication.foia.address,
                'sent_datetime': timezone.now(),
            }
        )
        single_pdf.seek(0)
        mail.pdf.save(
            '{}.pdf'.format(snail.communication.pk),
            ContentFile(single_pdf.read()),
        )

        # append to the bulk pdf
        single_pdf.seek(0)
        bulk_merger.append(single_pdf)
        # ensure we align for double sided printing
        if PdfFileReader(single_pdf).getNumPages() % 2 == 1:
            blank.seek(0)
            bulk_merger.append(blank)

    # preprend the cover sheet
    cover_pdf = CoverPDF(cover_info)
    cover_pdf.generate()
    if cover_pdf.page % 2 == 1:
        cover_pdf.add_page()
    bulk_merger.merge(0, StringIO(cover_pdf.output(dest='S')))

    bulk_pdf = StringIO()
    bulk_merger.write(bulk_pdf)
    bulk_pdf.seek(0)

    conn = S3Connection(
        settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY
    )
    bucket = conn.get_bucket(settings.AWS_STORAGE_BUCKET_NAME)
    key = Key(bucket)
    key.key = pdf_name
    key.set_contents_from_file(bulk_pdf)
    key.set_canned_acl('public-read')


@task(
    ignore_result=True,
    max_retries=5,
    name='muckrock.task.tasks.create_zoho_ticket'
)
def create_zoho_ticket(flag_pk, **kwargs):
    """Create a zoho ticket from a flag"""
    flag = FlaggedTask.objects.get(pk=flag_pk)
    if flag.resolved:
        return
    try:
        zoho_id = flag.create_zoho_ticket()
    except RequestException as exc:
        raise create_zoho_ticket.retry(
            countdown=(2 ** create_zoho_ticket.request.retries) * 300 +
            randint(0, 300),
            args=[flag_pk],
            kwargs=kwargs,
            exc=exc,
        )
    else:
        flag.resolve(form_data={'zoho_id': zoho_id})


@periodic_task(
    run_every=crontab(hour=4, minute=0),
    name='muckrock.task.tasks.cleanup_zoho_flags',
)
def cleanup_zoho_flags():
    """Find any flags that failed to make it to zoho and try again"""
    for flag in FlaggedTask.objects.filter(resolved=False):
        create_zoho_ticket.delay(flag.pk)
