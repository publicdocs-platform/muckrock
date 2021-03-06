# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2226-12-26 01:13
from __future__ import unicode_literals

# Django
from django.db import migrations


def deactivate(apps, schema_editor):
    Organization = apps.get_model('organization', 'Organization')
    Organization.objects.filter(
        active=False, individual=False
    ).update(org_type=0)


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0011_auto_20181019_1449'),
    ]

    operations = [
        migrations.RunPython(
            deactivate,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
