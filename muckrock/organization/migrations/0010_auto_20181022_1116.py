# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2018-10-22 15:16
from __future__ import unicode_literals

# Django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0009_auto_20181018_1701'),
    ]

    operations = [
        migrations.AlterField(
            model_name='organization',
            name='slug',
            field=models.SlugField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name='organization',
            name='name',
            field=models.CharField(max_length=255),
        ),
    ]
