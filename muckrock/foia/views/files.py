"""FOIA views for handling files"""

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView

from djangosecure.decorators import frame_deny_exempt

from muckrock.foia.models import FOIAFile, FOIARequest
from muckrock.views import PaginationMixin

@method_decorator(frame_deny_exempt, name='dispatch')
class FileEmbedView(DetailView):
    """Presents an embeddable view for a single file."""
    model = FOIAFile
    template_name = 'foia/file/embed.html'


class FOIAFileListView(PaginationMixin, ListView):
    """Presents a paginated list of files."""
    model = FOIAFile
    template_name = 'foia/file/list.html'
    foia = None

    def dispatch(self, request, *args, **kwargs):
        """Prevent unauthorized users from viewing the files."""
        foia = self.get_foia()
        if not foia.has_perm(request.user, 'view'):
            raise Http404()
        return super(FOIAFileListView, self).dispatch(request, *args, **kwargs)

    def get_foia(self):
        """Returns the FOIA Request for the files. Caches it as an attribute."""
        if self.foia is None:
            self.foia = get_object_or_404(FOIARequest, pk=self.kwargs.get('idx'))
        return self.foia

    def get_queryset(self):
        foia = self.get_foia()
        queryset = super(FOIAFileListView, self).get_queryset()
        return (queryset.filter(foia=foia)
            .select_related('foia')
            .select_related('foia__user')
            .select_related('foia__agency')
            .select_related('foia__jurisdiction')
            .prefetch_related('foia__edit_collaborators')
            .prefetch_related('foia__read_collaborators'))

    def get_context_data(self, **kwargs):
        context = super(FOIAFileListView, self).get_context_data(**kwargs)
        context['foia'] = self.get_foia()
        return context
