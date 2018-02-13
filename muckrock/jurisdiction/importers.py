"""
Custom importer for jurisdiction laws
"""
# Django
from django.conf import settings

# Third Party
import unicodecsv as csv
from boto.s3.connection import S3Connection
from smart_open import smart_open

# MuckRock
from muckrock.jurisdiction.models import Jurisdiction, Law


def import_laws(file_name):
    """Import laws from a spreadsheet"""
    # pylint: disable=too-many-locals
    conn = S3Connection(
        settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY
    )
    bucket = conn.get_bucket('muckrock')
    key = bucket.get_key(file_name)
    with smart_open(key) as law_file:
        law_reader = csv.reader(law_file)
        for (
            jurisdiction_name,
            citation,
            url,
            name,
            shortname,
            key_dates,
            days,
            use_business_days,
            waiver,
            has_appeal,
            requires_proxy,
            fee_schedule,
            trade_secrets,
            penalties,
            cover_judicial,
            cover_legislative,
            cover_executive,
        ) in law_reader:
            jurisdiction = Jurisdiction.objects.exclude(level='l').filter(
                name=jurisdiction_name
            )
            law, _ = Law.objects.update_or_create(
                jurisdiction=jurisdiction,
                defaults={
                    'citation': citation,
                    'url': url,
                    'name': name,
                    'shortname': shortname,
                    'days': int(days) if days else None,
                    'use_business_days': use_business_days == 'TRUE',
                    'waiver': waiver,
                    'has_appeal': has_appeal == 'TRUE',
                    'requires_proxy': requires_proxy == 'TRUE',
                    'fee_schedule': fee_schedule == 'TRUE',
                    'trade_secrets': trade_secrets == 'TRUE',
                    'penalties': penalties == 'TRUE',
                    'cover_judicial': cover_judicial == 'TRUE',
                    'cover_legislative': cover_legislative == 'TRUE',
                    'cover_executive': cover_executive == 'TRUE',
                },
            )
            if key_dates:
                law.years.all().delete()
                for date in key_dates.split(';'):
                    reason, year = date.split()
                    law.years.create(reason=reason, year=year)
