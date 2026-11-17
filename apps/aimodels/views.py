import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require

from . import checks, runtime
from .forms import ModelProviderForm
from .models import KINDS, ModelProvider


@login_required
def provider_list(request):
    require(request.user, "model.manage")
    providers = ModelProvider.objects.all()
    groups = []
    for kind, label in KINDS:
        rows = [p for p in providers if p.kind == kind]
        active = runtime.config_for(kind, use_cache=False)
        if rows or kind in ("llm", "asr", "diarization", "denoise", "embedding", "reranker"):
            groups.append({"kind": kind, "label": label, "providers": rows, "active": active})
    return render(
        request,
        "aimodels/provider_list.html",
        {"groups": groups, "total": providers.count(), "form": ModelProviderForm()},
    )


@login_required
def provider_form(request, pk=None):
    require(request.user, "model.manage")
    provider = get_object_or_404(ModelProvider, pk=pk) if pk else None
    form = ModelProviderForm(request.POST or None, request.FILES or None, instance=provider)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        runtime.invalidate()
        messages.success(request, f"{obj.name} saved.")
        return redirect(obj.get_absolute_url())
    return render(request, "aimodels/provider_form.html", {"form": form, "provider": provider})


@login_required
def provider_detail(request, pk):
    require(request.user, "model.manage")
    provider = get_object_or_404(ModelProvider, pk=pk)
    return render(
        request,
        "aimodels/provider_detail.html",
        {"provider": provider, "active": runtime.config_for(provider.kind, use_cache=False)},
    )


@login_required
@require_POST
def provider_delete(request, pk):
    require(request.user, "model.manage")
    provider = get_object_or_404(ModelProvider, pk=pk)
    name = provider.name
    provider.delete()
    runtime.invalidate()
    messages.success(request, f"{name} deleted. The environment default takes over for that job.")
    return redirect("aimodels:list")


@login_required
@require_POST
def provider_make_default(request, pk):
    require(request.user, "model.manage")
    provider = get_object_or_404(ModelProvider, pk=pk)
    provider.is_default = True
    provider.is_active = True
    provider.save()
    runtime.invalidate()
    messages.success(request, f"{provider.name} is now used for {provider.get_kind_display()}.")
    return redirect("aimodels:list")


@login_required
@require_POST
def provider_drop_cert(request, pk, field):
    require(request.user, "model.manage")
    provider = get_object_or_404(ModelProvider, pk=pk)
    if field not in ("ca_cert", "client_cert", "client_key"):
        return redirect(provider.get_absolute_url())
    file = getattr(provider, field)
    if file:
        file.delete(save=False)
        setattr(provider, field, None)
        provider.save()
        runtime.invalidate()
        messages.success(request, "Certificate removed.")
    return redirect(provider.get_absolute_url())


@login_required
@require_POST
def provider_test(request, pk):
    """Reach the endpoint with the stored key and certificates."""
    require(request.user, "model.manage")
    provider = get_object_or_404(ModelProvider, pk=pk)
    mode = json.loads(request.body or "{}").get("mode", "connect")

    if mode == "generate" and provider.kind == "llm":
        ok, message, latency = checks.generate(provider)
        served = []
    else:
        ok, message, latency, served = checks.probe(provider)
    provider.record_check(ok, "" if ok else message, latency, served)

    return JsonResponse(
        {"ok": ok, "message": message, "latency_ms": latency, "models": served[:20], "mode": mode}
    )
