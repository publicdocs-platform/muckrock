# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2017-12-11 15:07
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataset', '0003_dataset_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='datafield',
            name='hidden',
            field=models.BooleanField(default=False),
        ),
    ]
