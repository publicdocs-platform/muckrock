# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2017-11-29 15:49
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0004_portal_created_timestamp'),
    ]

    operations = [
        migrations.AlterField(
            model_name='portal',
            name='type',
            field=models.CharField(choices=[(b'foiaonline', b'FOIAonline'), (b'govqa', b'GovQA'), (b'nextrequest', b'NextRequest'), (b'foiaxpress', b'FOIAXpress'), (b'fbi', b'FBI eFOIPA Portal'), (b'other', b'Other')], max_length=11),
        ),
    ]
