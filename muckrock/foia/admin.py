"""
Admin registration for FOIA models
"""

from django import forms
from django.conf.urls import url
from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.core.urlresolvers import reverse
from django.db.models import Count, Max
from django.http import HttpResponseRedirect
from django.shortcuts import render, get_object_or_404

from autocomplete_light import shortcuts as autocomplete_light
from datetime import date, datetime, timedelta
from reversion.admin import VersionAdmin
import os

from muckrock.agency.models import Agency
from muckrock.communication.admin import (
        EmailCommunicationInline,
        FaxCommunicationInline,
        MailCommunicationInline,
        WebCommunicationInline,
        )
from muckrock.communication.models import (
        Address,
        EmailAddress,
        PhoneNumber,
        )
from muckrock.crowdfund.models import Crowdfund
from muckrock.foia.models import (
        FOIARequest,
        FOIAMultiRequest,
        FOIAFile,
        FOIACommunication,
        FOIANote,
        STATUS,
        OutboundAttachment,
        )
from muckrock.foia.tasks import (
        upload_document_cloud,
        set_document_cloud_pages,
        autoimport,
        submit_multi_request,
        )
from muckrock.jurisdiction.models import Jurisdiction

# These inhereit more than the allowed number of public methods
# pylint: disable=too-many-public-methods

class FOIAFileAdminForm(forms.ModelForm):
    """Form to validate document only has ASCII characters in it"""

    def __init__(self, *args, **kwargs):
        super(FOIAFileAdminForm, self).__init__(*args, **kwargs)
        self.clean_title = self._validate('title')
        self.clean_source = self._validate('source')
        self.clean_description = self._validate('description')

    class Meta:
        # pylint: disable=too-few-public-methods
        model = FOIAFile
        fields = '__all__'

    @staticmethod
    def _only_ascii(text):
        """Ensure's that text only contains ASCII characters"""
        non_ascii = ''.join(c for c in text if ord(c) >= 128)
        if non_ascii:
            raise forms.ValidationError('Field contains non-ASCII characters: %s' % non_ascii)

    def _validate(self, field):
        """Make a validator for field"""

        def inner():
            """Ensure field only has ASCII characters"""
            data = self.cleaned_data[field]
            self._only_ascii(data)
            return data

        return inner


class FOIAFileInline(admin.StackedInline):
    """FOIA File Inline admin options"""
    model = FOIAFile
    form = FOIAFileAdminForm
    readonly_fields = ('doc_id', 'pages', 'access', 'source')
    fields = (
            ('title', 'date'),
            'ffile',
            'description',
            ('doc_id', 'pages'),
            ('source', 'access'),
            )
    extra = 0


class FOIACommunicationAdminForm(forms.ModelForm):
    """Form for comm inline"""

    from_user = autocomplete_light.ModelChoiceField(
            'UserAutocomplete',
            queryset=User.objects.all(),
            )
    to_user = autocomplete_light.ModelChoiceField(
            'UserAutocomplete',
            queryset=User.objects.all(),
            )

    class Meta:
        model = FOIACommunication
        fields = '__all__'


class FOIACommunicationAdmin(VersionAdmin):
    """FOIA Communication admin options"""
    model = FOIACommunication
    form = FOIACommunicationAdminForm
    readonly_fields = ('foia_link', 'confirmed')
    fieldsets = (
            (None, {
                'fields': (
                    'foia_link',
                    ('from_user', 'to_user'),
                    ('subject', 'date'),
                    'communication',
                    'status',
                    ('response', 'autogenerated', 'thanks', 'full_html'),
                    )
                }),
            ('Deprecated', {
                'classes': ('collapse',),
                'fields': (
                    'from_who',
                    'to_who',
                    'priv_from_who',
                    'priv_to_who',
                    'delivered',
                    'fax_id',
                    ),
                'description': 'These values are no longer actively used.  '
                'They are here to view on old data only.  If you find yourself '
                'needing to look here often, something is probably wrong and '
                'you should file a bug',
                }))
    inlines = (
            FOIAFileInline,
            EmailCommunicationInline,
            FaxCommunicationInline,
            MailCommunicationInline,
            WebCommunicationInline,
            )

    def foia_link(self, obj):
        """Link to this communication's FOIA admin"""
        # pylint: disable=no-self-use
        link = reverse('admin:foia_foiarequest_change', args=(obj.foia.pk,))
        return '<a href="%s">%s</a>' % (link, obj.foia.title)
    foia_link.allow_tags = True
    foia_link.short_description = 'FOIA Request'

    def save_formset(self, request, form, formset, change):
        """Actions to take while saving inline instances"""
        # pylint: disable=no-self-use
        # pylint: disable=unused-argument

        instances = formset.save(commit=False)
        for instance in instances:
            # only way to tell if its new or not is to check the db
            change = True
            try:
                formset.model.objects.get(pk=instance.pk)
            except formset.model.DoesNotExist:
                change = False

            instance.foia = instance.comm.foia
            instance.save()

            # its new, so notify the user about it
            if not change:
                instance.comm.foia.update(instance.anchor())

            upload_document_cloud.apply_async(
                    args=[instance.pk, change], countdown=30)

        formset.save_m2m()

        for obj in formset.deleted_objects:
            obj.delete()


class FOIACommunicationInline(admin.StackedInline):
    """FOIA Communication Inline admin options"""
    # pylint: disable=no-self-use
    model = FOIACommunication
    fk_name = 'foia'
    extra = 1
    readonly_fields = (
            'get_delivered',
            'confirmed_datetime',
            'error',
            'file_count',
            'file_names',
            'open',
            )
    show_change_link = True
    form = FOIACommunicationAdminForm
    fields = (
            ('from_user', 'to_user'),
            ('subject', 'date'),
            'communication',
            ('file_count', 'file_names'),
            'status',
            'get_delivered',
            ('confirmed_datetime', 'open', 'error'),
            ('response', 'autogenerated', 'thanks', 'full_html'),
            )

    def file_count(self, instance):
        """File count for this communication"""
        return instance.files_count

    def file_names(self, instance):
        """All file's names for this communication"""
        return ', '.join(os.path.basename(f.ffile.name) for f in
                instance.files.all())

    def confirmed_datetime(self, instance):
        """Date time when this was confirmed as being sent"""
        if instance.email_confirmed_datetime:
            return instance.email_confirmed_datetime
        elif instance.fax_confirmed_datetime:
            return instance.fax_confirmed_datetime
        else:
            return None

    def open(self, instance):
        """Was this communicaion opened?"""
        return instance.opens_count > 0
    open.boolean = True

    def error(self, instance):
        """Did this communication have an error sending?"""
        return instance.email_errors_count > 0 or instance.fax_errors_count > 0
    error.boolean = True

    def get_queryset(self, request):
        return (super(FOIACommunicationInline, self)
                .get_queryset(request)
                .prefetch_related(
                    'files',
                    'emails',
                    'faxes',
                    'mails',
                    'web_comms',
                    )
                .annotate(
                    files_count=Count('files'),
                    opens_count=Count('emails__opens'),
                    email_errors_count=Count('emails__errors'),
                    fax_errors_count=Count('faxes__errors'),
                    email_confirmed_datetime=Max('emails__confirmed_datetime'),
                    fax_confirmed_datetime=Max('faxes__confirmed_datetime'),
                    )
                )


class FOIANoteAdminForm(forms.ModelForm):
    """Form for note inline"""

    author = autocomplete_light.ModelChoiceField(
            'UserAutocomplete',
            label='Author',
            queryset=User.objects.all())

    class Meta:
        model = FOIANote
        fields = '__all__'


class FOIANoteInline(admin.TabularInline):
    """FOIA Notes Inline admin options"""
    model = FOIANote
    form = FOIANoteAdminForm
    extra = 1


class FOIARequestAdminForm(forms.ModelForm):
    """Form to include custom choice fields"""

    jurisdiction = autocomplete_light.ModelChoiceField(
            'JurisdictionAdminAutocomplete',
            queryset=Jurisdiction.objects.all(),
            )
    agency = autocomplete_light.ModelChoiceField(
            'AgencyAdminAutocomplete',
            queryset=Agency.objects.all(),
            )
    user = autocomplete_light.ModelChoiceField(
            'UserAutocomplete',
            queryset=User.objects.all(),
            )
    parent = autocomplete_light.ModelChoiceField(
            'FOIARequestAdminAutocomplete',
            queryset=FOIARequest.objects.all(),
            required=False,
            )
    read_collaborators = autocomplete_light.ModelMultipleChoiceField(
            'UserAutocomplete',
            queryset=User.objects.all(),
            required=False,
            )
    edit_collaborators = autocomplete_light.ModelMultipleChoiceField(
            'UserAutocomplete',
            queryset=User.objects.all(),
            required=False,
            )
    email = autocomplete_light.ModelChoiceField(
            'EmailAddressAutocomplete',
            queryset=EmailAddress.objects.all(),
            required=False,
            )
    fax = autocomplete_light.ModelChoiceField(
            'PhoneNumberFaxAutocomplete',
            queryset=PhoneNumber.objects.filter(type='fax'),
            required=False,
            )
    address = autocomplete_light.ModelChoiceField(
            'AddressAutocomplete',
            queryset=Address.objects.all(),
            required=False,
            )
    cc_emails = autocomplete_light.ModelMultipleChoiceField(
            'EmailAddressAutocomplete',
            queryset=EmailAddress.objects.all(),
            required=False,
            )
    crowdfund = autocomplete_light.ModelChoiceField(
            'CrowdfundAutocomplete',
            queryset=Crowdfund.objects.all(),
            required=False,
            )
    multirequest = autocomplete_light.ModelChoiceField(
            'FOIAMultiRequestAutocomplete',
            queryset=FOIAMultiRequest.objects.all(),
            required=False,
            )

    class Meta:
        # pylint: disable=too-few-public-methods
        model = FOIARequest
        fields = '__all__'


class FOIARequestAdmin(VersionAdmin):
    """FOIA Request admin options"""
    change_list_template = 'admin/foia/foiarequest/change_list.html'
    prepopulated_fields = {'slug': ('title',)}
    list_display = ('title', 'user', 'status', 'agency', 'jurisdiction')
    list_filter = ['status']
    list_select_related = ('agency', 'jurisdiction', 'user')
    search_fields = ['title', 'description', 'tracking_id', 'mail_id']
    readonly_fields = ['mail_id']
    filter_horizontal = ('read_collaborators', 'edit_collaborators')
    inlines = [FOIACommunicationInline, FOIANoteInline]
    save_on_top = True
    form = FOIARequestAdminForm
    formats = ['xls', 'csv']
    # pylint: disable=protected-access
    headers = [f.name for f in FOIARequest._meta.fields] + ['total_pages']
    # pylint: enable=protected-access
    exclude = ['old_email', 'other_emails']

    def save_model(self, request, obj, form, change):
        """Actions to take when a request is saved from the admin"""
        # pylint: disable=no-self-use
        # pylint: disable=unused-argument

        # If changing to completed and embargoed, set embargo date to 30 days out
        if obj.status in ['done', 'partial'] and obj.embargo and not obj.date_embargo:
            obj.date_embargo = date.today() + timedelta(30)

        # NOT saving here if changed
        # saving after formset so that we can check for updates there first
        if not change:
            obj.save()

    def save_formset(self, request, form, formset, change):
        """Actions to take while saving inline instances"""
        # pylint: disable=no-self-use
        # pylint: disable=unused-argument

        if formset.model == FOIANote:
            formset.save()
            # check for foia updates here so that communication updates take priority
            # (Notes are last)
            foia = form.instance
            old_foia = FOIARequest.objects.get(pk=foia.pk)
            if foia.status != old_foia.status:
                foia.update()
            foia.update_dates()
            foia.save()
            return

        # check communications for new ones to notify the user of an update
        instances = formset.save(commit=False)
        for instance in instances:
            # only way to tell if its new or not is to check the db
            change = True
            try:
                formset.model.objects.get(pk=instance.pk)
            except formset.model.DoesNotExist:
                change = False

            instance.save()
            # its new, so notify the user about it
            if not change:
                instance.foia.update(instance.anchor())

        formset.save_m2m()

    def get_urls(self):
        """Add custom URLs here"""
        urls = super(FOIARequestAdmin, self).get_urls()
        my_urls = [
                url(r'^process/$', self.admin_site.admin_view(self.process),
                    name='foia-admin-process'),
                url(r'^followup/$', self.admin_site.admin_view(self.followup),
                    name='foia-admin-followup'),
                url(r'^undated/$', self.admin_site.admin_view(self.undated),
                    name='foia-admin-undated'),
                url(r'^send_update/(?P<idx>\d+)/$',
                    self.admin_site.admin_view(self.send_update),
                    name='foia-admin-send-update'),
                url(r'^retry_pages/(?P<idx>\d+)/$',
                    self.admin_site.admin_view(self.retry_pages),
                    name='foia-admin-retry-pages'),
                url(r'^set_status/(?P<idx>\d+)/(?P<status>\w+)/$',
                    self.admin_site.admin_view(self.set_status),
                    name='foia-admin-set-status'),
                url(r'^autoimport/$',
                    self.admin_site.admin_view(self.autoimport),
                    name='foia-admin-autoimport'),
                ]
        return my_urls + urls

    def _list_helper(self, request, foias, action):
        """List all the requests that need to be processed"""
        # pylint: disable=no-self-use
        paginator = Paginator(foias, 10)
        try:
            page = paginator.page(request.GET.get('page'))
        except PageNotAnInteger:
            page = paginator.page(1)
        except EmptyPage:
            page = paginator.page(paginator.num_pages)
        return render(
                request,
                'admin/foia/admin_process.html',
                {'page': page, 'action': action},
                )

    def process(self, request):
        """List all the requests that need to be processed"""
        # pylint: disable=no-self-use
        foias = list(FOIARequest.objects.filter(status='submitted'))
        return self._list_helper(request, foias, 'Process')

    def followup(self, request):
        """List all the requests that need to be followed up"""
        # pylint: disable=no-self-use
        foias = list(FOIARequest.objects.get_manual_followup())
        return self._list_helper(request, foias, 'Follow Up')

    def undated(self, request):
        """List all the requests that have undated documents or files"""
        # pylint: disable=no-self-use
        foias = list(FOIARequest.objects.get_undated())
        return self._list_helper(request, foias, 'Undated')

    def send_update(self, request, idx):
        """Manually send the user an update notification"""
        # pylint: disable=no-self-use

        foia = get_object_or_404(FOIARequest, pk=idx)
        foia.update()
        messages.info(request, 'An update notification has been set to the user, %s' % foia.user)
        return HttpResponseRedirect(reverse('admin:foia_foiarequest_change', args=[foia.pk]))

    def retry_pages(self, request, idx):
        """Retry getting the page count"""
        # pylint: disable=no-self-use

        docs = FOIAFile.objects.filter(foia=idx, pages=0)
        for doc in docs:
            if doc.is_doccloud():
                set_document_cloud_pages.apply_async(args=[doc.pk])

        messages.info(request, 'Attempting to set the page count for %d documents... Please '
                               'wait while the Document Cloud servers are being accessed'
                               % docs.count())
        return HttpResponseRedirect(reverse('admin:foia_foiarequest_change', args=[idx]))

    def autoimport(self, request):
        """Autoimport documents from S3"""
        # pylint: disable=no-self-use
        autoimport.apply_async()
        messages.info(request, 'Auotimport started')
        return HttpResponseRedirect(reverse('admin:foia_foiarequest_changelist'))

    def set_status(self, request, idx, status):
        """Set the status of the request"""
        # pylint: disable=no-self-use

        try:
            foia = FOIARequest.objects.get(pk=idx)
        except FOIARequest.DoesNotExist:
            messages.error(request, '%s is not a valid FOIA Request' % idx)
            return HttpResponseRedirect(reverse('admin:foia_foiarequest_changelist'))

        if status not in [s for (s, _) in STATUS]:
            messages.error(request, '%s is not a valid status' % status)
            return HttpResponseRedirect(reverse('admin:foia_foiarequest_change', args=[foia.pk]))

        foia.status = status
        foia.update()
        dateord = request.GET.get('dateord')
        if status in ['rejected', 'no_docs', 'done', 'abandoned'] and dateord:
            foia.date_done = date.fromordinal(int(dateord))
        foia.save()
        last_comm = foia.last_comm()
        last_comm.status = status
        last_comm.save()

        try:
            if status in ['ack', 'processed', 'appealing']:
                comm_pk = request.GET.get('comm_pk')
                comm = FOIACommunication.objects.get(pk=comm_pk)
                if comm.foia == foia:
                    comm.date = datetime.now()
                    comm.save()
        except FOIACommunication.DoesNotExist:
            pass

        messages.success(request, 'Status set to %s' % foia.get_status_display())
        return HttpResponseRedirect(reverse('admin:foia_foiarequest_change', args=[foia.pk]))


class FOIAMultiRequestAdminForm(forms.ModelForm):
    """Form for multi request admin"""

    user = autocomplete_light.ModelChoiceField(
            'UserAutocomplete',
            queryset=User.objects.all(),
            )
    agencies = autocomplete_light.ModelMultipleChoiceField(
            'AgencyAdminAutocomplete',
            queryset=Agency.objects.all(),
            required=False,
            )

    class Meta:
        model = FOIAMultiRequest
        fields = '__all__'


class FOIAMultiRequestAdmin(VersionAdmin):
    """FOIA Multi Request admin options"""
    change_form_template = 'admin/foia/multifoiarequest/change_form.html'
    prepopulated_fields = {'slug': ('title',)}
    list_display = ('title', 'user', 'status')
    search_fields = ['title', 'requested_docs']
    form = FOIAMultiRequestAdminForm

    def get_urls(self):
        """Add custom URLs here"""
        urls = super(FOIAMultiRequestAdmin, self).get_urls()
        my_urls = [url(
            r'^submit/(?P<idx>\d+)/$',
            self.admin_site.admin_view(self.submit),
            name='multifoia-admin-submit',
            )]
        return my_urls + urls

    def submit(self, request, idx):
        """Submit the multi request"""
        # pylint: disable=no-self-use

        get_object_or_404(FOIAMultiRequest, pk=idx)
        submit_multi_request.apply_async(args=[idx])

        messages.info(request, 'Multi request is being submitted...')
        return HttpResponseRedirect(reverse('admin:foia_foiamultirequest_changelist'))


class OutboundAttachmentAdminForm(forms.ModelForm):
    """Form for outbound attachment admin"""

    foia = autocomplete_light.ModelChoiceField(
            'FOIARequestAdminAutocomplete',
            queryset=FOIARequest.objects.all(),
            )
    user = autocomplete_light.ModelChoiceField(
            'UserAutocomplete',
            queryset=User.objects.all(),
            )

    class Meta:
        model = OutboundAttachment
        fields = '__all__'


class OutboundAttachmentAdmin(VersionAdmin):
    """Outbound Attachment admin options"""
    search_fields = ('foia__title', 'user__username')
    list_display = ('foia', 'user', 'ffile', 'date_time_stamp')
    list_select_related = ('foia', 'user')
    date_hierarchy = 'date_time_stamp'
    form = OutboundAttachmentAdminForm


admin.site.register(FOIARequest, FOIARequestAdmin)
admin.site.register(FOIACommunication, FOIACommunicationAdmin)
admin.site.register(FOIAMultiRequest, FOIAMultiRequestAdmin)
admin.site.register(OutboundAttachment, OutboundAttachmentAdmin)
