__copyright__ = "Copyright 2024-2025 Public Library of Science"
__author__ = "Tim Tamimi @ PLOS"
__license__ = "AGPL v3"
__maintainer__ = "Public Library of Science"

from django.urls import path

from plugins.publication_fee_quotation import views

urlpatterns = [
    # Manager/Admin URLs
    path(
        "manager/",
        views.manager,
        name="fee_quotation_manager",
    ),
    path(
        "manager/regenerate-secret/",
        views.regenerate_webhook_secret,
        name="fee_quotation_regenerate_secret",
    ),
    path(
        "manager/quotation/<int:quotation_id>/",
        views.quotation_detail,
        name="fee_quotation_detail",
    ),
    
    # Author-facing URLs
    path(
        "request/<int:article_id>/",
        views.request_quotation,
        name="fee_quotation_request",
    ),
    path(
        "status/<int:quotation_id>/",
        views.check_quotation_status,
        name="fee_quotation_status",
    ),
    
    # Webhook URL (note: this is journal-specific)
    path(
        "webhook/<str:journal_code>/",
        views.webhook_callback,
        name="fee_quotation_webhook",
    ),
]

