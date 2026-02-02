__copyright__ = "Copyright 2024-2025 Public Library of Science"
__author__ = "Tim Tamimi @ PLOS"
__license__ = "AGPL v3"
__maintainer__ = "Public Library of Science"

import hashlib
import hmac
import json

from django.contrib import messages
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from plugins.janeway_publication_fee_quotation_plugin import forms, logic, models
from security.decorators import editor_user_required
from submission.models import Article
from utils.logger import get_logger

logger = get_logger(__name__)


@editor_user_required
def manager(request):
    """
    Manager view for configuring the fee quotation plugin.
    """
    config, created = models.FeeQuotationConfiguration.objects.get_or_create(
        journal=request.journal,
    )

    if request.method == "POST":
        form = forms.FeeQuotationConfigurationForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Fee quotation configuration saved successfully.")
            return redirect(reverse("fee_quotation_manager"))
    else:
        form = forms.FeeQuotationConfigurationForm(instance=config)

    # Get recent quotations for this journal
    recent_quotations = models.FeeQuotation.objects.filter(
        article__journal=request.journal
    ).select_related("article", "author")[:20]

    template = "janeway_publication_fee_quotation_plugin/manager.html"
    context = {
        "form": form,
        "config": config,
        "recent_quotations": recent_quotations,
    }

    return render(request, template, context)


@editor_user_required
def regenerate_webhook_secret(request):
    """
    Regenerate the webhook secret for the journal's fee quotation configuration.
    """
    config = get_object_or_404(
        models.FeeQuotationConfiguration,
        journal=request.journal,
    )

    if request.method == "POST":
        import secrets

        config.webhook_secret = secrets.token_urlsafe(32)
        config.save()
        messages.success(request, "Webhook secret regenerated successfully.")

    return redirect(reverse("fee_quotation_manager"))


@require_POST
def request_quotation(request, article_id):
    """
    Request a fee quotation from the third-party service.
    Called via AJAX when the author clicks the request quotation button.
    """
    article = get_object_or_404(
        Article,
        pk=article_id,
        owner=request.user,
        journal=request.journal,
    )

    try:
        quotation = logic.request_fee_quotation(article, request.user, request)

        if quotation.status == models.FeeQuotationStatus.ERROR:
            return JsonResponse(
                {
                    "success": False,
                    "error": quotation.error_message or "Failed to request quotation.",
                },
                status=400,
            )

        return JsonResponse(
            {
                "success": True,
                "quotation_id": str(quotation.id),
                "quotation_url": quotation.quotation_url,
                "status": quotation.status,
            }
        )

    except Exception as e:
        logger.error(f"Error requesting fee quotation: {e}")
        return JsonResponse(
            {
                "success": False,
                "error": "An unexpected error occurred. Please try again.",
            },
            status=500,
        )


@require_GET
def check_quotation_status(request, quotation_id):
    """
    Check the status of a fee quotation.
    Called via AJAX polling to update the UI.
    """
    quotation = get_object_or_404(
        models.FeeQuotation,
        pk=quotation_id,
        author=request.user,
    )

    return JsonResponse(
        {
            "quotation_id": str(quotation.id),
            "status": quotation.status,
            "is_accepted": quotation.is_accepted,
            "can_proceed": quotation.can_proceed,
            "quotation_url": quotation.quotation_url,
        }
    )


@csrf_exempt
@require_POST
def webhook_callback(request, journal_code):
    """
    Webhook endpoint to receive acceptance/decline notifications from the third-party service.

    Expected payload:
    {
        "quote_id": "string",
        "status": "accepted" or "declined",
        "timestamp": "ISO 8601 timestamp",
        ... additional data ...
    }

    The quote_id must match the external_quote_id stored in Janeway.
    The webhook should include a signature header for verification.
    """
    from journal.models import Journal

    try:
        journal = Journal.objects.get(code=journal_code)
    except Journal.DoesNotExist:
        logger.warning(f"Webhook received for unknown journal: {journal_code}")
        return HttpResponseBadRequest("Invalid journal code")

    try:
        config = journal.fee_quotation_config
    except models.FeeQuotationConfiguration.DoesNotExist:
        logger.warning(f"Webhook received but no config for journal: {journal_code}")
        return HttpResponseBadRequest("Fee quotation not configured for this journal")

    # Verify webhook signature if present
    signature = request.headers.get("X-Webhook-Signature")
    if config.webhook_secret and signature:
        expected_signature = hmac.new(
            config.webhook_secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            logger.warning(f"Invalid webhook signature for journal: {journal_code}")
            return HttpResponseBadRequest("Invalid signature")

    # Parse the payload
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning(f"Invalid JSON in webhook payload for journal: {journal_code}")
        return HttpResponseBadRequest("Invalid JSON payload")

    # Get the quote_id from the payload
    quote_id = payload.get("quote_id")
    if not quote_id:
        logger.warning(
            f"Missing quote_id in webhook payload for journal: {journal_code}"
        )
        return HttpResponseBadRequest("Missing quote_id")

    # Look up the quotation by external_quote_id
    # Exclude voided/error/expired quotations - the webhook should only affect active ones
    # If multiple exist (e.g., after voiding), get the most recent one
    try:
        quotation = (
            models.FeeQuotation.objects.filter(
                external_quote_id=quote_id,
                article__journal=journal,
            )
            .exclude(
                status__in=[
                    models.FeeQuotationStatus.VOIDED,
                    models.FeeQuotationStatus.ERROR,
                    models.FeeQuotationStatus.EXPIRED,
                ]
            )
            .order_by("-created")
            .first()
        )

        if not quotation:
            raise models.FeeQuotation.DoesNotExist()
    except models.FeeQuotation.DoesNotExist:
        logger.warning(f"Quotation not found for quote_id: {quote_id}")
        return HttpResponseBadRequest("Quotation not found")

    # Update the quotation status
    status = payload.get("status", "").lower()
    if status == "accepted":
        quotation.mark_accepted(webhook_payload=payload)
        logger.info(f"Quotation {quote_id} marked as accepted")
    elif status == "declined":
        quotation.mark_declined(webhook_payload=payload)
        logger.info(f"Quotation {quote_id} marked as declined")
    else:
        logger.warning(f"Unknown status in webhook: {status}")
        return HttpResponseBadRequest(f"Unknown status: {status}")

    return JsonResponse({"success": True, "status": quotation.status})


@editor_user_required
def quotation_detail(request, quotation_id):
    """
    View details of a specific fee quotation.
    """
    quotation = get_object_or_404(
        models.FeeQuotation,
        pk=quotation_id,
        article__journal=request.journal,
    )

    template = "janeway_publication_fee_quotation_plugin/quotation_detail.html"
    context = {
        "quotation": quotation,
    }

    return render(request, template, context)
