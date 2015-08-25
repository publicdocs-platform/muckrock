"""
Views for the QandA application
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.db.models import Count
from django.shortcuts import render_to_response, get_object_or_404, redirect
from django.template import RequestContext
from django.template.defaultfilters import slugify
from django.views.generic.detail import DetailView

import actstream
from datetime import datetime
from rest_framework import viewsets, status
from rest_framework.decorators import detail_route
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from muckrock.qanda.models import Question, Answer
from muckrock.qanda.forms import QuestionForm, AnswerForm
from muckrock.qanda.serializers import QuestionSerializer, QuestionPermissions
from muckrock.tags.models import Tag
from muckrock.views import MRFilterableListView

class QuestionList(MRFilterableListView):
    """List of unanswered questions"""
    model = Question
    title = 'Questions & Answers'
    template_name = 'lists/question_list.html'

    def get_context_data(self, **kwargs):
        """Adds an info message to the context"""
        context = super(QuestionList, self).get_context_data(**kwargs)
        info_msg = ('Looking for FOIA advice? Post your questions here '
                    'to get invaluable insight from MuckRock\'s community '
                    'of public records pros. Have a technical support '
                    'or customer service issue? Those should be reported '
                    'either using the "Report" button on the request page '
                    'or simply by emailing <a href="mailto:info@muckrock.com">'
                    'info@muckrock.com</a>.')
        messages.info(self.request, info_msg)
        return context

class UnansweredQuestionList(QuestionList):
    """List of unanswered questions"""
    def get_queryset(self):
        objects = super(UnansweredQuestionList, self).get_queryset()
        return objects.annotate(num_answers=Count('answers')).filter(num_answers=0)

class Detail(DetailView):
    """Question detail view"""
    model = Question

    def post(self, request, **kwargs):
        """Edit the question or answer"""
        # pylint: disable=unused-argument

        question = self.get_object()
        obj_type = request.POST.get('object')

        if obj_type == 'question':
            self._question(request, question)
        elif obj_type == 'answer':
            try:
                self._answer(request)
            except Answer.DoesNotExist:
                pass

        tags = request.POST.get('tags')
        if tags:
            tag_set = set()
            for tag in tags.split(','):
                tag = Tag.normalize(tag)
                if not tag:
                    continue
                new_tag, _ = Tag.objects.get_or_create(name=tag)
                tag_set.add(new_tag)
            self.get_object().tags.set(*tag_set)
            self.get_object().save()
            messages.success(request, 'Your tags have been saved to this question.')

        return redirect(question)

    def _question(self, request, question):
        """Edit the question"""
        # pylint: disable=no-self-use
        if request.user == question.user or request.user.is_staff:
            question.question = request.POST.get('question')
            question.save()
            messages.success(request, 'Your question is updated.')
        else:
            messages.error(request, 'You may only edit your own questions.')

    def _answer(self, request):
        """Edit an answer"""
        # pylint: disable=no-self-use
        answer = Answer.objects.get(pk=request.POST.get('answer-pk'))
        if request.user == answer.user or request.user.is_staff:
            answer.answer = request.POST.get('answer')
            answer.save()
            messages.success(request, 'Your answer is updated.')
        else:
            messages.error(request, 'You may only edit your own answers.')

    def get_context_data(self, **kwargs):
        context = super(Detail, self).get_context_data(**kwargs)
        user = self.request.user
        if user.is_authenticated() and context['object'].followed_by.filter(user=user):
            context['follow_label'] = 'Unfollow'
        else:
            context['follow_label'] = 'Follow'
        context['sidebar_admin_url'] = reverse('admin:qanda_question_change',
            args=(context['object'].pk,))
        return context


@login_required
def create_question(request):
    """Create a question"""

    if request.method == 'POST':
        form = QuestionForm(request.POST)
        if form.is_valid():
            question = form.save(commit=False)
            question.slug = slugify(question.title)
            question.user = request.user
            question.date = datetime.now()
            question.save()
            question.notify_new()
            question.followed_by.add(request.user.profile)
            return redirect(question)
    else:
        form = QuestionForm()

    return render_to_response('forms/question.html', {'form': form},
                              context_instance=RequestContext(request))

@login_required
def follow(request, slug, idx):
    """Follow or unfollow a question"""
    question = get_object_or_404(Question, slug=slug, id=idx)
    followers = actstream.models.followers(question)
    if question.user == request.user:
        messages.error(request, 'You automatically follow questions you ask.')
    elif request.user in followers:
        actstream.actions.unfollow(request.user, question)
        messages.success(request, 'You are no longer following this question.')
    else:
        actstream.actions.follow(request.user, question, actor_only=False)
        messages.success(request, 'You are now following this question.')
    return redirect(question)

@login_required
def create_answer(request, slug, idx):
    """Create an answer"""

    question = get_object_or_404(Question, slug=slug, pk=idx)

    if request.method == 'POST':
        form = AnswerForm(request.POST)
        if form.is_valid():
            answer = form.save(commit=False)
            answer.user = request.user
            answer.date = datetime.now()
            answer.question = question
            answer.save()
            answer.question.notify_update()
            return redirect(answer.question)
    else:
        form = AnswerForm()

    return render_to_response(
        'forms/answer.html',
        {'form': form, 'question': question},
        context_instance=RequestContext(request)
    )

@login_required
def subscribe(request):
    """Subscribe or unsubscribe to new questions"""
    profile = request.user.profile

    if profile.follow_questions:
        profile.follow_questions = False
        messages.info(request, 'You are now unsubscribed from new question notifications')
    else:
        profile.follow_questions = True
        messages.success(request, 'You are now subscribed to new question notifications')
    profile.save()

    return redirect('question-index')

class QuestionViewSet(viewsets.ModelViewSet):
    """API views for Question"""
    # pylint: disable=too-many-public-methods
    # pylint: disable=C0103
    # pylint: disable=too-many-ancestors
    queryset = Question.objects.all()
    serializer_class = QuestionSerializer
    permission_classes = (QuestionPermissions,)
    filter_fields = ('title', 'foia',)

    def pre_save(self, obj):
        """Auto fill fields on create"""
        if not obj.pk:
            obj.date = datetime.now()
            obj.slug = slugify(obj.title)
            obj.user = self.request.user
        return super(QuestionViewSet, self).pre_save(obj)

    @detail_route(permission_classes=(IsAuthenticated,))
    def answer(self, request, pk=None):
        """Answer a question"""
        try:
            question = Question.objects.get(pk=pk)
            self.check_object_permissions(request, question)
            Answer.objects.create(user=request.user, date=datetime.now(), question=question,
                                  answer=request.DATA['answer'])
            return Response(
                {'status': 'Answer submitted'},
                status=status.HTTP_200_OK
            )
        except Question.DoesNotExist:
            return Response(
                {'status': 'Not Found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except KeyError:
            return Response(
                {'status': 'Missing data - Please supply answer'},
                status=status.HTTP_400_BAD_REQUEST
            )
