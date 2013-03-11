from diff_match_patch import diff_match_patch
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseNotFound
from django.shortcuts import get_object_or_404
from django.template import RequestContext, loader
from django.template.response import TemplateResponse
from django.utils.translation import ugettext as _
from django.utils.safestring import mark_safe
from urllib import urlencode, quote
from . import models, forms, utils

def view(request, title, revision_pk=None):
	url_title = utils.urlize_title(title)
	if title != url_title:
		if revision_pk:
			return HttpResponseRedirect(reverse('djiki-page-revision',
						kwargs={'title': url_title, 'revision_pk': revision_pk}))
		return HttpResponseRedirect(reverse('djiki-page-view', kwargs={'title': url_title}))
	page_title = utils.deurlize_title(title)
	auth = utils.get_auth_backend()
	try:
		page = models.Page.objects.get(title=page_title)
	except models.Page.DoesNotExist:
		t = loader.get_template('djiki/not_found.html')
		c = RequestContext(request, {'title': page_title})
		return HttpResponseNotFound(t.render(c))
	if not auth.can_view(request, page):
		raise PermissionDenied
	if revision_pk:
		if not auth.can_view_history(request, page):
			raise PermissionDenied
		try:
			revision = page.revisions.get(pk=revision_pk)
		except models.PageRevision.DoesNotExist:
			return HttpResponseNotFound()
		messages.info(request, mark_safe(_("The version you are viewing is not the latest one, "
				"but represents an older revision of this page, which may have been "
				"significantly modified. If it is not what you intended to view, "
				"<a href=\"%(url)s\">proceed to the latest version</a>.") % {
					'url': reverse('djiki-page-view', kwargs={'title': url_title})}))
	else:
		revision = page.last_revision()
	if request.REQUEST.get('raw', ''):
		response = HttpResponse(mimetype='text/plain')
		response['Content-Disposition'] = 'attachment; filename=%s.txt' % quote(title.encode('utf-8'))
		response.write(revision.content)
		return response
	return TemplateResponse(request, 'djiki/view.html',
			{'page': page, 'revision': revision})

def edit(request, title):
	url_title = utils.urlize_title(title)
	if title != url_title:
		return HttpResponseRedirect(reverse('djiki-page-edit', kwargs={'title': url_title}))
	page_title = utils.deurlize_title(title)
	auth = utils.get_auth_backend()
	try:
		page = models.Page.objects.get(title=page_title)
		last_content = page.last_revision().content
		if not auth.can_edit(request, page):
			raise PermissionDenied
	except models.Page.DoesNotExist:
		page = models.Page(title=page_title)
		last_content = ''
		if not auth.can_create(request, page):
			raise PermissionDenied
	revision = models.PageRevision(page=page,
			author=request.user if request.user.is_authenticated() else None)
	form = forms.PageEditForm(
			data=request.POST or None, instance=revision, page=page,
			initial={'content': last_content})
	preview_content = None
	if request.method == 'POST':
		is_preview = request.POST.get('action') == 'preview'
		if form.is_valid():
			if is_preview:
				preview_content = form.cleaned_data.get('content', form.data['content'])
				messages.info(request, mark_safe(_("The content you see on this page is shown only as "
						"a preview. <strong>No changes have been saved yet.</strong> Please "
						"review the modifications and use the <em>Save</em> button to store "
						"them permanently.")))
			else:
				form.save()
				return HttpResponseRedirect(
						reverse('djiki-page-view', kwargs={'title': url_title}))
	return TemplateResponse(request, 'djiki/edit.html',
			{'form': form, 'page': page, 'preview_content': preview_content})

def history(request, title):
	url_title = utils.urlize_title(title)
	if title != url_title:
		return HttpResponseRedirect(reverse('djiki-page-history', kwargs={'title': url_title}))
	page_title = utils.deurlize_title(title)
	page = get_object_or_404(models.Page, title=page_title)
	auth = utils.get_auth_backend()
	if not auth.can_view_history(request, page):
		raise PermissionDenied
	history = page.revisions.order_by('-created')
	return TemplateResponse(request, 'djiki/history.html', {'page': page, 'history': history})


def diff(request, title):
	url_title = utils.urlize_title(title)
	if title != url_title:
		return HttpResponseNotFound()
	page_title = utils.deurlize_title(title)
	page = get_object_or_404(models.Page, title=page_title)
	auth = utils.get_auth_backend()
	if not auth.can_view_history(request, page):
		raise PermissionDenied
	try:
		from_rev = page.revisions.get(pk=request.REQUEST['from_revision_pk'])
		to_rev = page.revisions.get(pk=request.REQUEST['to_revision_pk'])
	except (KeyError, models.Page.DoesNotExist):
		return HttpResponseNotFound()
	dmp = diff_match_patch()
	diff = dmp.diff_compute(from_rev.content, to_rev.content, True, 2)
	return TemplateResponse(request, 'djiki/diff.html',
			{'page': page, 'from_revision': from_rev, 'to_revision': to_rev, 'diff': diff})

def revert(request, title, revision_pk):
	if not allow_anonymous_edits() and not request.user.is_authenticated():
		raise PermissionDenied
	url_title = utils.urlize_title(title)
	if title != url_title:
		return HttpResponseRedirect(
				reverse('djiki-page-revert', kwargs={'title': url_title, 'revision_pk': revision_pk}))
	page_title = utils.deurlize_title(title)
	page = get_object_or_404(models.Page, title=page_title)
	auth = utils.get_auth_backend()
	if not auth.can_edit(request, page):
		raise PermissionDenied
	src_revision = get_object_or_404(models.PageRevision, page=page, pk=revision_pk)
	new_revision = models.PageRevision(page=page,
			author=request.user if request.user.is_authenticated() else None)
	if request.method == 'POST':
		form = forms.PageEditForm(data=request.POST or None, instance=new_revision, page=page)
		if form.is_valid():
			form.save()
			return HttpResponseRedirect(reverse('djiki-page-view', kwargs={'title': url_title}))
	else:
		if src_revision.author:
			description = _("Reverted to revision of %(time)s by %(user)s.") % \
					{'time': src_revision.created, 'user': src_revision.user.username}
		else:
			description = _("Reverted to anonymous revision of %(time)s.") % \
					{'time': src_revision.created}
		form = forms.PageEditForm(data=request.POST or None, instance=new_revision, page=page,
				initial={'content': src_revision.content, 'description': description})
	return TemplateResponse(request, 'djiki/edit.html',
			{'page': page, 'form': form, 'src_revision': src_revision})

def undo(request, title, revision_pk):
	url_title = utils.urlize_title(title)
	if title != url_title:
		return HttpResponseRedirect(
				reverse('djiki-page-undo', kwargs={'title': url_title, 'revision_pk': revision_pk}))
	page_title = utils.deurlize_title(title)
	page = get_object_or_404(models.Page, title=page_title)
	auth = utils.get_auth_backend()
	if not auth.can_edit(request, page):
		raise PermissionDenied
	src_revision = get_object_or_404(models.PageRevision, page=page, pk=revision_pk)
	new_revision = models.PageRevision(page=page,
			author=request.user if request.user.is_authenticated() else None)
	if request.method == 'POST':
		form = forms.PageEditForm(data=request.POST or None, instance=new_revision, page=page)
		if form.is_valid():
			form.save()
			return HttpResponseRedirect(reverse('djiki-page-view', kwargs={'title': url_title}))
	else:
		if src_revision.author:
			description = _("Undid revision of %(time)s by %(user)s.") % \
					{'time': src_revision.created, 'user': src_revision.user.username}
		else:
			description = _("Undid anonymous revision of %(time)s.") % {'time': src_revision.created}
		try:
			prev_revision = models.PageRevision.objects\
					.filter(page=page, created__lt=src_revision.created)\
					.order_by('-created')[0]
			prev_content = prev_revision.content
		except IndexError:
			prev_content = ''
		dmp = diff_match_patch()
		rdiff = dmp.patch_make(src_revision.content, prev_content)
		content, results = dmp.patch_apply(rdiff, page.last_revision().content)
		if False in results:
			messages.warning(request, _("It was impossible to automatically undo the change "
					"you have selected. Perhaps the page has been modified too much in the "
					"meantime. Review the following content comparison, which represents the "
					"change you tried to undo, and apply the changes manually to the latest "
					"revision."))
			urldata = {'to_revision_pk': src_revision.pk}
			if prev_revision:
				urldata['from_revision_pk'] = prev_revision.pk
			return HttpResponseRedirect("%s?%s" % (
					reverse('djiki-page-diff', kwargs={'title': url_title}),
					urlencode(urldata)))
		form = forms.PageEditForm(data=request.POST or None, page=page,
				initial={'content': content, 'description': description})
	return TemplateResponse(request, 'djiki/edit.html', {'page': page, 'form': form})

def image_new(request):
	auth = utils.get_auth_backend()
	if not auth.can_create(request, models.Image()):
		raise PermissionDenied
	form = forms.NewImageUploadForm(data=request.POST or None, files=request.FILES or None)
	if request.method == 'POST':
		if form.is_valid():
			form.save()
			return HttpResponseRedirect(
					reverse('djiki-image-view', kwargs={'name': form.instance.image.name}))
	return TemplateResponse(request, 'djiki/image_edit.html', {'form': form})

def image_view(request, name):
	url_name = utils.urlize_title(name)
	if name != url_name:
		return HttpResponseRedirect(reverse('djiki-image-view', kwargs={'name': url_name}))
	image_name = utils.deurlize_title(name)
	image = get_object_or_404(models.Image, name=image_name)
	auth = utils.get_auth_backend()
	if not auth.can_view(request, image):
		raise PermissionDenied
	return TemplateResponse(request, 'djiki/image_view.html', {'image': image})

def image_edit(request, name):
	url_name = utils.urlize_title(name)
	if name != url_name:
		return HttpResponseRedirect(reverse('djiki-image-edit', kwargs={'name': url_name}))
	image_name = utils.deurlize_title(name)
	image = get_object_or_404(models.Image, name=image_name)
	auth = utils.get_auth_backend()
	if not auth.can_edit(request, image):
		raise PermissionDenied
	revision = models.ImageRevision(image=image,
			author=request.user if request.user.is_authenticated() else None)
	form = forms.ImageUploadForm(data=request.POST or None, files=request.FILES or None,
			instance=revision, image=image)
	if request.method == 'POST':
		if form.is_valid():
			form.save()
			return HttpResponseRedirect(
					reverse('djiki-image-view', kwargs={'name': url_name}))
	return TemplateResponse(request, 'djiki/image_edit.html', {'form': form})

def image_history(request, name):
	url_name = utils.urlize_title(name)
	if name != url_name:
		return HttpResponseRedirect(reverse('djiki-image-view', kwargs={'name': url_name}))
	image_name = utils.deurlize_title(name)
	image = get_object_or_404(models.Image, name=image_name)
	auth = utils.get_auth_backend()
	if not auth.can_view_history(request, image):
		raise PermissionDenied
	history = image.revisions.order_by('-created')
	return TemplateResponse(request, 'djiki/image_history.html', {'image': image, 'history': history})
