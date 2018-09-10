# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2018-09-05 20:04
from __future__ import unicode_literals

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('crowdsource', '0020_auto_20180807_1001'),
    ]

    operations = [
        migrations.AddField(
            model_name='crowdsource',
            name='registration',
            field=models.CharField(choices=[(b'required', b'Required'), (b'off', b'Off'), (b'optional', b'Optional')], default=b'required', help_text=b'Is registration required to complete this assignment?', max_length=8),
        ),
        migrations.AddField(
            model_name='crowdsourceresponse',
            name='ip_address',
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='crowdsourceresponse',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='crowdsource_responses', to=settings.AUTH_USER_MODEL),
        ),
    ]
