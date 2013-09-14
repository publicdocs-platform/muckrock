"""
Views for the Agency application
"""

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count
from django.http import Http404
from django.shortcuts import render_to_response, get_object_or_404, redirect
from django.template import RequestContext

from datetime import date

from muckrock.agency.forms import AgencyForm
from muckrock.agency.models import Agency
from muckrock.foia.models import FOIARequest
from muckrock.jurisdiction.models import Jurisdiction
from muckrock.jurisdiction.views import collect_stats, flag_helper
from muckrock.sidebar.models import Sidebar

def detail(request, jurisdiction, jidx, slug, idx):
    """Details for an agency"""

    jmodel = get_object_or_404(Jurisdiction, slug=jurisdiction, pk=jidx)
    agency = get_object_or_404(Agency, jurisdiction=jmodel, slug=slug, pk=idx)

    if not agency.approved:
        raise Http404()

    context = {'agency': agency}
    if request.user.is_anonymous():
        context['sidebar'] = Sidebar.objects.get_text('anon_agency')
    else:
        context['sidebar'] = Sidebar.objects.get_text('agency')
    collect_stats(agency, context)

    return render_to_response('agency/agency_detail.html', context,
                              context_instance=RequestContext(request))

def list_(request):
    """List of popular agencies"""
    agencies = Agency.objects.annotate(num_requests=Count('foiarequest')) \
                             .order_by('-num_requests')[:10]
    context = {'agencies': agencies}

    return render_to_response('agency/agency_list.html', context,
                              context_instance=RequestContext(request))

@login_required
def update(request, jurisdiction, jidx, slug, idx):
    """Allow the user to fill in some information about new agencies they create"""

    jmodel = get_object_or_404(Jurisdiction, slug=jurisdiction, pk=jidx)
    agency = get_object_or_404(Agency, jurisdiction=jmodel, slug=slug, pk=idx)

    if agency.user != request.user or agency.approved:
        messages.error(request, 'You may only edit your own agencies which have '
                                'not been approved yet')
        return redirect('foia-mylist', view='all')

    if request.method == 'POST':
        form = AgencyForm(request.POST, instance=agency)
        if form.is_valid():
            form.save()
            messages.success(request, 'Agency information saved.')
            foia_pk = request.GET.get('foia')
            foia = FOIARequest.objects.filter(pk=foia_pk)
            if foia:
                return redirect(foia[0])
            else:
                return redirect('foia-mylist', view='all')
    else:
        form = AgencyForm(instance=agency)

    return render_to_response('agency/agency_form.html', {'form': form},
                              context_instance=RequestContext(request))

@login_required
def flag(request, jurisdiction, jidx, slug, idx):
    """Flag a correction for an agency's information"""

    jmodel = get_object_or_404(Jurisdiction, slug=jurisdiction, pk=jidx)
    agency = get_object_or_404(Agency, jurisdiction=jmodel, slug=slug, pk=idx)

    return flag_helper(request, agency, 'agency')

def redirect_old(request, jurisdiction, slug, idx, action):
    """Redirect old urls to new urls"""
    # pylint: disable=W0612
    # pylint: disable=W0613

    # some jurisdiction slugs changed, just ignore the jurisdiction slug passed in
    agency = get_object_or_404(Agency, pk=idx)
    jurisdiction = agency.jurisdiction.slug
    jidx = agency.jurisdiction.pk

    if action == 'view':
        return redirect('/agency/%(jurisdiction)s-%(jidx)s/%(slug)s-%(idx)s/' % locals())

    return redirect('/agency/%(jurisdiction)s-%(jidx)s/%(slug)s-%(idx)s/%(action)s/' % locals())

def stale(request):
    """List all stale agencies"""

    agencies = Agency.objects.all()
    stale_agencies = []

    for agency in agencies:
        foias = agency.foiarequest_set.get_open()
        if foias:
            latest_responses = [foia.last_comm_date() for foia in foias if foia.last_comm_date()]
            if latest_responses:
                latest_response = (date.today() - max(latest_responses)).days
                if latest_response >= 30:
                    stale_agencies.append((agency, len(foias), latest_response))
                    continue
            else:
                stale_agencies.append((agency, len(foias), None))
                continue
        if agency.expired():
            stale_agencies.append((agency, None, None))

    return render_to_response('agency/agency_stale.html',
                              {'stale_agencies': stale_agencies},
                              context_instance=RequestContext(request))

