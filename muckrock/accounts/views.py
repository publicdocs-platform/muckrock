"""
Views for the accounts application
"""

# Django
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView, ListView, TemplateView

# Standard Library
import json
import logging
import sys
from urllib import urlencode

# Third Party
import stripe
from djangosecure.decorators import frame_deny_exempt
from rest_framework import viewsets
from rest_framework.authtoken.models import Token
from rest_framework.permissions import (
    DjangoModelPermissionsOrAnonReadOnly,
    IsAdminUser,
)

# MuckRock
from muckrock.accounts.filters import ProxyFilterSet
from muckrock.accounts.forms import (
    BillingPreferencesForm,
    BuyRequestForm,
    EmailSettingsForm,
    OrgPreferencesForm,
    ProfileSettingsForm,
    ReceiptForm,
)
from muckrock.accounts.mixins import BuyRequestsMixin
from muckrock.accounts.models import (
    ACCT_TYPES,
    Notification,
    ReceiptEmail,
    RecurringDonation,
    Statistics,
)
from muckrock.accounts.serializers import StatisticsSerializer, UserSerializer
from muckrock.accounts.utils import mixpanel_event
from muckrock.agency.models import Agency
from muckrock.communication.models import EmailAddress
from muckrock.core.views import MRFilterListView
from muckrock.crowdfund.models import RecurringCrowdfundPayment
from muckrock.foia.models import FOIARequest
from muckrock.message.email import TemplateEmail
from muckrock.message.tasks import (
    failed_payment,
    send_charge_receipt,
    send_invoice_receipt,
)
from muckrock.news.models import Article
from muckrock.organization.models import Organization
from muckrock.project.models import Project

logger = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY


def account_logout(request):
    """Logs a user out of their account and redirects to squarelet's logout page"""
    if 'id_token' in request.session:
        params = {
            'id_token_hint':
                request.session['id_token'],
            'post_logout_redirect_uri':
                'http://{}/'.format(settings.MUCKROCK_URL),
        }
        redirect_url = '{}/openid/end-session?{}'.format(
            settings.SQUARELET_URL, urlencode(params)
        )
    else:
        redirect_url = 'index'
    logout(request)
    messages.success(request, 'You have successfully logged out.')
    return redirect(redirect_url)


class AccountsView(TemplateView):
    """
    Displays the list of payment plans.
    If user is logged out, it lets them register for any plan.
    If user is logged in, it lets them up- or downgrade their account to any plan.
    """
    # XXX if logged out, just redirect to squarelet?
    template_name = 'accounts/plans.html'

    def get_context_data(self, **kwargs):
        """Returns a context based on whether the user is logged in or logged out."""
        context = super(AccountsView, self).get_context_data(**kwargs)
        logged_in = self.request.user.is_authenticated()
        if logged_in:
            context['acct_type'] = self.request.user.profile.acct_type
            context['email'] = self.request.user.email
            context['org'] = (
                Organization.objects.filter(owner=self.request.user).first()
            )
        context['stripe_pk'] = settings.STRIPE_PUB_KEY
        context['logged_in'] = logged_in
        return context

    def post(self, request, **kwargs):
        """Handle upgrades and downgrades of accounts"""
        try:
            action = request.POST['action']
            account_actions = {'downgrade': downgrade, 'upgrade': upgrade}
            account_actions[action](request)
        except KeyError as exception:
            logger.error(exception)
            messages.error(request, 'No available action was specified.')
        except AttributeError as exception:
            logger.error(exception)
            error_msg = 'You cannot be logged out and modify your account.'
            messages.error(request, error_msg)
        except ValueError as exception:
            logger.error(exception)
            messages.error(request, exception)
        except stripe.error.CardError:
            # card declined
            logger.warn('Card was declined.')
            messages.error(request, 'Your card was declined')
        return self.render_to_response(self.get_context_data())


def upgrade(request):
    """Upgrades the user from a Basic to a Professional account."""
    if not request.user.is_authenticated():
        raise AttributeError('Cannot upgrade an anonymous user.')
    is_pro_user = request.user.profile.acct_type in ['pro', 'proxy']
    is_org_owner = Organization.objects.filter(owner=request.user).exists()
    if is_pro_user:
        raise ValueError(
            'Cannot upgrade this account, it is already Professional.'
        )
    if is_org_owner:
        raise ValueError(
            'Cannot upgrade this account, it owns an organization.'
        )
    token = request.POST.get('stripe_token')
    if not token:
        raise ValueError(
            'Cannot upgrade this account, no Stripe token provided.'
        )
    request.user.profile.start_pro_subscription(token)
    mixpanel_event(request, 'Pro Subscription Started')


def downgrade(request):
    """Downgrades the user from a Professional to a Basic account."""
    if not request.user.is_authenticated():
        raise AttributeError('Cannot downgrade an anonymous user.')
    if request.user.profile.acct_type != 'pro':
        raise ValueError(
            'Cannot downgrade this account, it is not Professional.'
        )
    request.user.profile.cancel_pro_subscription()
    mixpanel_event(request, 'Pro Subscription Cancelled')


@method_decorator(login_required, name='dispatch')
class ProfileSettings(TemplateView):
    """Update a users information"""
    template_name = 'accounts/settings.html'

    def post(self, request, **kwargs):
        """Handle form processing"""
        # pylint: disable=unused-argument
        settings_forms = {
            'profile': ProfileSettingsForm,
            'email': EmailSettingsForm,
            'billing': BillingPreferencesForm,
            'org': OrgPreferencesForm,
        }
        action = request.POST.get('action')
        receipt_form = None
        if action == 'receipt':
            receipt_form = ReceiptForm(request.POST)
            success = self._handle_receipt_form(receipt_form)
            if success:
                return redirect('acct-settings')
        elif action == 'cancel-donations':
            self._handle_cancel_payments('donations', 'cancel-donations')
            return redirect('acct-settings')
        elif action == 'cancel-crowdfunds':
            self._handle_cancel_payments(
                'recurring_crowdfund_payments',
                'cancel-crowdfunds',
            )
            return redirect('acct-settings')
        elif action:
            form = settings_forms[action]
            form = form(
                request.POST,
                request.FILES,
                instance=request.user.profile,
            )
            if form.is_valid():
                form.save()
                messages.success(request, 'Your settings have been updated.')
                return redirect('acct-settings')

        # form was invalid
        context = self.get_context_data(receipt_form=receipt_form)
        return self.render_to_response(context)

    def _handle_receipt_form(self, receipt_form):
        """Process the receipt form"""
        if receipt_form.is_valid():
            new_emails = receipt_form.cleaned_data['emails'].split('\n')
            new_emails = {e.strip() for e in new_emails}
            old_emails = {
                r.email
                for r in self.request.user.receipt_emails.all()
            }
            ReceiptEmail.objects.filter(
                user=self.request.user,
                email__in=(old_emails - new_emails),
            ).delete()
            ReceiptEmail.objects.bulk_create([
                ReceiptEmail(user=self.request.user, email=e)
                for e in new_emails - old_emails
            ])
            messages.success(self.request, 'Your settings have been updated.')
            return True
        else:
            return False

    def _handle_cancel_payments(self, attr, arg):
        """Handle cancelling recurring donations or crowdfunds"""
        payments = (
            getattr(self.request.user, attr)
            .filter(pk__in=self.request.POST.getlist(arg))
        )
        attr_type = {
            'donations': 'Donation',
            'recurring_crowdfund_payments': 'Recurring Crowdfund',
        }
        for payment in payments:
            payment.cancel()
            mixpanel_event(
                self.request,
                'Cancel {}'.format(attr_type[attr]),
                {'Amount': payment.amount},
            )
        msg = attr.replace('_', ' ')
        if payments:
            messages.success(
                self.request,
                'The selected {} have been cancelled.'.format(msg),
            )
        else:
            messages.warning(
                self.request,
                'No {} were selected to be cancelled.'.format(msg),
            )

    def get_context_data(self, **kwargs):
        """Returns context for the template"""
        context = super(ProfileSettings, self).get_context_data(**kwargs)
        user_profile = self.request.user.profile
        email_initial = {'email': self.request.user.email}
        receipt_initial = {
            'emails':
                '\n'
                .join(r.email for r in self.request.user.receipt_emails.all())
        }
        profile_form = ProfileSettingsForm(instance=user_profile)
        email_form = EmailSettingsForm(
            initial=email_initial,
            instance=user_profile,
        )
        org_form = OrgPreferencesForm(instance=user_profile)
        receipt_form = (
            kwargs.get('receipt_form') or ReceiptForm(initial=receipt_initial)
        )
        current_plan = dict(ACCT_TYPES)[user_profile.acct_type]
        if user_profile.organization:
            current_plan = 'Organization'
        donations = RecurringDonation.objects.filter(user=self.request.user)
        crowdfunds = RecurringCrowdfundPayment.objects.filter(
            user=self.request.user
        )
        context.update({
            'stripe_pk': settings.STRIPE_PUB_KEY,
            'profile_form': profile_form,
            'email_form': email_form,
            'receipt_form': receipt_form,
            'org_form': org_form,
            'current_plan': current_plan,
            'credit_card': user_profile.card(),
            'donations': donations,
            'crowdfunds': crowdfunds,
        })
        return context


class ProfileView(BuyRequestsMixin, FormView):
    """View a user's profile"""
    template_name = 'accounts/profile.html'
    form_class = BuyRequestForm

    def dispatch(self, request, *args, **kwargs):
        """Get the user and redirect if neccessary"""
        # pylint: disable=attribute-defined-outside-init
        username = kwargs.get('username')
        if username is None:
            return redirect('acct-profile', username=request.user.username)
        self.user = get_object_or_404(User, username=username, is_active=True)
        return super(ProfileView, self).dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Check for cancel pro before checking form"""
        if (
            request.user.is_staff and request.POST.get('action') == 'cancel-pro'
        ):
            self.user.profile.cancel_pro_subscription()
            mixpanel_event(request, 'Pro Subscription Cancelled')
            messages.success(request, 'Pro account has been cancelled')
            return redirect('acct-profile', username=self.user.username)
        else:
            return super(ProfileView, self).post(request, *args, **kwargs)

    def get_initial(self):
        """Set the form label"""
        if self.user == self.request.user:
            return {'stripe_label': 'Buy'}
        else:
            return {'stripe_label': 'Gift'}

    def get_form_kwargs(self):
        """Give the form the current user"""
        kwargs = super(ProfileView, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        """Get data for the profile page"""
        context_data = super(ProfileView, self).get_context_data(**kwargs)
        org = self.user.profile.organization
        show_org_link = (
            org and (
                not org.private or self.request.user.is_staff or (
                    self.request.user.is_authenticated()
                    and self.request.user.profile.is_member_of(org)
                )
            )
        )
        requests = (
            FOIARequest.objects.filter(composer__user=self.user)
            .get_viewable(self.request.user)
            .select_related('agency__jurisdiction__parent__parent')
        )
        recent_requests = requests.order_by('-composer__datetime_submitted')[:5]
        recent_completed = (
            requests.filter(status='done').order_by('-datetime_done')[:5]
        )
        articles = Article.objects.get_published().filter(authors=self.user)[:5]
        projects = Project.objects.get_for_contributor(self.user).get_visible(
            self.request.user
        )[:3]
        context_data.update({
            'user_obj':
                self.user,
            'profile':
                self.user.profile,
            'org':
                org,
            'show_org_link':
                show_org_link,
            'projects':
                projects,
            'requests': {
                'all': requests,
                'recent': recent_requests,
                'completed': recent_completed
            },
            'articles':
                articles,
            'stripe_pk':
                settings.STRIPE_PUB_KEY,
            'sidebar_admin_url':
                reverse('admin:auth_user_change', args=(self.user.pk,)),
            'api_token':
                Token.objects.get_or_create(user=self.user)[0],
        })
        return context_data

    def form_valid(self, form):
        """Buy requests"""
        self.buy_requests(form, recipient=self.user)
        return redirect('acct-profile', username=self.user.username)


@csrf_exempt
def stripe_webhook(request):
    """Handle webhooks from stripe"""
    if request.method != "POST":
        return HttpResponseNotAllowed(['POST'])

    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    try:
        if settings.STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload,
                sig_header,
                settings.STRIPE_WEBHOOK_SECRET,
            )
        else:
            event = json.loads(request.body)

        event_id = event['id']
        event_type = event['type']
        if event_type.startswith(('charge', 'invoice')):
            event_object_id = event['data']['object'].get('id', '')
        else:
            event_object_id = ''
    except (TypeError, ValueError, SyntaxError) as exception:
        logging.error(
            'Stripe Webhook: Error parsing JSON: %s',
            exception,
            exc_info=sys.exc_info(),
        )
        return HttpResponseBadRequest()
    except KeyError as exception:
        logging.error(
            'Stripe Webhook: Unexpected structure: %s in %s',
            exception,
            event,
            exc_info=sys.exc_info(),
        )
        return HttpResponseBadRequest()
    except stripe.error.SignatureVerificationError as exception:
        logging.error(
            'Stripe Webhook: Signature Verification Error: %s',
            sig_header,
            exc_info=sys.exc_info(),
        )
        return HttpResponseBadRequest()
    # If we've made it this far, then the webhook message was successfully sent!
    # Now it's up to us to act on it.
    success_msg = (
        'Received Stripe webhook\n'
        '\tfrom:\t%(address)s\n'
        '\tid:\t%(id)s\n'
        '\ttype:\t%(type)s\n'
        '\tdata:\t%(data)s\n'
    ) % {
        'address': request.META['REMOTE_ADDR'],
        'id': event_id,
        'type': event_type,
        'data': event
    }
    logger.info(success_msg)
    if event_type == 'charge.succeeded':
        send_charge_receipt.delay(event_object_id)
    elif event_type == 'invoice.payment_succeeded':
        send_invoice_receipt.delay(event_object_id)
    elif event_type == 'invoice.payment_failed':
        failed_payment.delay(event_object_id)
    return HttpResponse()


class UserViewSet(viewsets.ModelViewSet):
    """API views for User"""
    # pylint: disable=too-many-public-methods
    queryset = (
        User.objects.order_by('id').prefetch_related('profile', 'groups')
    )
    serializer_class = UserSerializer
    permission_classes = (IsAdminUser,)
    filter_fields = ('username', 'profile__full_name', 'email', 'is_staff')


class StatisticsViewSet(viewsets.ModelViewSet):
    """API views for Statistics"""
    # pylint: disable=too-many-public-methods
    queryset = Statistics.objects.all()
    serializer_class = StatisticsSerializer
    permission_classes = (DjangoModelPermissionsOrAnonReadOnly,)
    filter_fields = ('date',)


@method_decorator(login_required, name='dispatch')
class NotificationList(ListView):
    """List of notifications for a user."""
    model = Notification
    template_name = 'accounts/notifications_all.html'
    context_object_name = 'notifications'
    title = 'All Notifications'

    def get_queryset(self):
        """Return all notifications for the user making the request."""
        return (
            super(NotificationList, self).get_queryset().for_user(
                self.request.user
            ).order_by('-datetime').select_related('action').prefetch_related(
                'action__actor',
                'action__target',
                'action__action_object',
            )
        )

    def get_paginate_by(self, queryset):
        """Paginates list by the return value"""
        try:
            per_page = int(self.request.GET.get('per_page'))
            return max(min(per_page, 100), 5)
        except (ValueError, TypeError):
            return 25

    def get_context_data(self, **kwargs):
        """Add the title to the context"""
        context = super(NotificationList, self).get_context_data(**kwargs)
        context['title'] = self.title
        return context

    def mark_all_read(self):
        """Mark all notifications for the view as read."""
        notifications = self.get_queryset()
        # to be more efficient, let's just get the unread ones
        notifications = notifications.get_unread()
        for notification in notifications:
            notification.mark_read()

    def post(self, request, *args, **kwargs):
        """Handle post actions to this view"""
        action = request.POST.get('action')
        if action == 'mark_all_read':
            self.mark_all_read()
        return self.get(request, *args, **kwargs)


@method_decorator(login_required, name='dispatch')
class UnreadNotificationList(NotificationList):
    """List only unread notifications for a user."""
    template_name = 'accounts/notifications_unread.html'
    title = 'Unread Notifications'

    def get_queryset(self):
        """Only return unread notifications."""
        notifications = super(UnreadNotificationList, self).get_queryset()
        return notifications.get_unread()


class ProxyList(MRFilterListView):
    """List of Proxies"""
    model = User
    filter_class = ProxyFilterSet
    title = 'Proxies'
    template_name = 'lists/proxy_list.html'
    default_sort = 'profile__state'
    sort_map = {
        'name': 'profile__full_name',
        'state': 'profile__state',
    }

    @method_decorator(user_passes_test(lambda u: u.is_staff))
    def dispatch(self, *args, **kwargs):
        """Staff only"""
        return super(ProxyList, self).dispatch(*args, **kwargs)

    def get_queryset(self):
        """Display all proxies"""
        objects = super(ProxyList, self).get_queryset()
        return (
            objects.filter(profile__acct_type='proxy')
            .select_related('profile')
        )


def agency_redirect_login(
    request, agency_slug, agency_idx, foia_slug, foia_idx
):
    """View to redirect agency users to the correct page or offer to resend
    them their login token"""

    agency = get_object_or_404(Agency, slug=agency_slug, pk=agency_idx)
    foia = get_object_or_404(
        FOIARequest,
        agency=agency,
        slug=foia_slug,
        pk=foia_idx,
    )

    if request.method == 'POST':
        email = request.POST.get('email', '')
        # valid if this email is associated with the agency
        valid = (
            EmailAddress.objects.fetch(email).agencies.filter(id=agency.pk)
            .exists()
        )
        if valid:
            msg = TemplateEmail(
                subject='Login Token',
                from_email='info@muckrock.com',
                to=[email],
                text_template='accounts/email/login_token.txt',
                html_template='accounts/email/login_token.html',
                extra_context={
                    'reply_link': foia.get_agency_reply_link(email=email),
                }
            )
            msg.send(fail_silently=False)
            messages.success(
                request,
                'Fresh login token succesfully sent to %s.  '
                'Please check your email' % email,
            )
        else:
            messages.error(request, 'Invalid email')
        return redirect(foia)

    authed = request.user.is_authenticated()
    agency_user = authed and request.user.profile.acct_type == 'agency'
    agency_match = agency_user and request.user.profile.agency == agency
    email = request.GET.get('email', '')
    # valid if this email is associated with the agency
    email_address = EmailAddress.objects.fetch(email)
    valid = (
        email_address is not None
        and email_address.agencies.filter(id=agency.pk).exists()
    )

    if agency_match:
        return redirect(foia)
    elif agency_user:
        return redirect('foia-agency-list')
    elif authed:
        return redirect('index')
    else:
        return render(
            request,
            'accounts/agency_redirect_login.html',
            {'email': email,
             'valid': valid},
        )


@frame_deny_exempt
def rp_iframe(request):
    """RP iframe for OIDC sesison management"""
    return render(
        request,
        'accounts/check_session_iframe.html',
        {'settings': settings},
    )
