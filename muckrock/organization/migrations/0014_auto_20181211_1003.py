# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2018-12-11 15:03
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0013_auto_20181030_1403'),
    ]

    operations = [
        migrations.RenameField(
            model_name='organization',
            old_name='_monthly_requests',
            new_name='requests_per_month',
        ),
    ]
