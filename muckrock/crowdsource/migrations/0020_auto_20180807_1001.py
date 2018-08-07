# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2018-08-07 14:01
from __future__ import unicode_literals

# Django
from django.db import migrations


def copy_original_values(apps, schema_editor):
    CrowdsourceValue = apps.get_model('crowdsource', 'CrowdsourceValue')
    for value in CrowdsourceValue.objects.all():
        value.original_value = value.value
        value.save()


class Migration(migrations.Migration):

    dependencies = [
        ('crowdsource', '0019_auto_20180807_0925'),
    ]

    operations = [
        migrations.RunPython(copy_original_values, migrations.RunPython.noop),
    ]
