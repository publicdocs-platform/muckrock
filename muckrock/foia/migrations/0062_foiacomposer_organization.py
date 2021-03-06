# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2018-10-17 16:27
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0007_auto_20181016_1324'),
        ('foia', '0061_foiarequest_deleted'),
    ]

    operations = [
        migrations.AddField(
            model_name='foiacomposer',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='composers', to='organization.Organization'),
        ),
    ]
