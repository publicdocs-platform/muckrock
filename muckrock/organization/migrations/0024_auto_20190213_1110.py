# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2019-02-13 16:10
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0023_auto_20190201_1602'),
    ]

    operations = [
        migrations.AlterField(
            model_name='organization',
            name='slug',
            field=models.SlugField(max_length=255, unique=True),
        ),
    ]
