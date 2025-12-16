__copyright__ = "Copyright 2024-2025 Public Library of Science"
__author__ = "Tim Tamimi @ PLOS"
__license__ = "AGPL v3"
__maintainer__ = "Public Library of Science"

import json
import secrets

from django.db import models
from django.utils import timezone


class FeeQuotationConfiguration(models.Model):
    """
    Stores the configuration for the fee quotation service per journal.
    """
    journal = models.OneToOneField(
        "journal.Journal",
        on_delete=models.CASCADE,
        related_name="fee_quotation_config",
    )
    
    # API Configuration
    api_url = models.URLField(
        max_length=500,
        help_text="The URL of the third-party fee quotation API endpoint.",
        blank=True,
    )
    
    request_body_template = models.TextField(
        blank=True,
        help_text=(
            "JSON template for the POST request body. "
            "Available placeholders: {{article_id}}, {{article_title}}, "
            "{{author_email}}, {{author_name}}, {{authors}}, {{journal_code}}, {{journal_name}}, "
            "{{section_name}}, {{section_id}}, {{callback_url}}. "
            "The {{authors}} placeholder outputs a JSON array of author objects with fields: "
            "address, authorIdentifiers, departmentName, emailAddress, firstName, lastName, "
            "middleName, institutionIdentifiers, institutionName, primary, salutation, suffix."
        ),
        default="""{
    "article_id": "{{article_id}}",
    "article_title": "{{article_title}}",
    "author_email": "{{author_email}}",
    "author_name": "{{author_name}}",
    "authors": "{{authors}}",
    "journal_code": "{{journal_code}}",
    "journal_name": "{{journal_name}}",
    "section_name": "{{section_name}}",
    "callback_url": "{{callback_url}}"
}""",
    )
    
    # Response configuration
    response_quote_id_field = models.CharField(
        max_length=100,
        default="quote_id",
        help_text=(
            "The field name in the API response that contains the quote ID. "
            "Supports nested fields using dot notation (e.g., 'data.quote_id')."
        ),
    )
    
    quotation_url_template = models.CharField(
        max_length=500,
        blank=True,
        help_text=(
            "URL template for the quotation page. Use {{quote_id}} as a placeholder. "
            "Example: https://billing.example.com/quote/{{quote_id}}"
        ),
        default="",
    )
    
    # Optional headers for authentication
    api_headers = models.TextField(
        blank=True,
        help_text=(
            "JSON object with additional headers to send with the API request. "
            "Example: {\"Authorization\": \"Bearer YOUR_TOKEN\"}"
        ),
        default="{}",
    )
    
    # Webhook secret for verifying callbacks
    webhook_secret = models.CharField(
        max_length=128,
        blank=True,
        help_text="Secret key to verify webhook callbacks from the third-party service.",
    )
    
    # UI Configuration
    button_text = models.CharField(
        max_length=100,
        default="View Fee Quotation",
        help_text="Text to display on the quotation button.",
    )
    
    instructions_text = models.TextField(
        blank=True,
        default=(
            "Before submitting, please review your estimated publication fees. "
            "Click the button below to view and accept the fee quotation."
        ),
        help_text="Instructions displayed to the author above the quotation button.",
    )
    
    # Feature flags
    is_enabled = models.BooleanField(
        default=False,
        help_text="Enable or disable the fee quotation feature for this journal.",
    )
    
    require_acceptance = models.BooleanField(
        default=True,
        help_text="If enabled, authors must accept the fee quotation before submitting.",
    )
    
    # Section-based conditions
    SECTION_MODE_ALL = "all"
    SECTION_MODE_INCLUDE = "include"
    SECTION_MODE_EXCLUDE = "exclude"
    SECTION_MODE_CHOICES = [
        (SECTION_MODE_ALL, "All sections"),
        (SECTION_MODE_INCLUDE, "Only selected sections"),
        (SECTION_MODE_EXCLUDE, "All except selected sections"),
    ]
    
    section_mode = models.CharField(
        max_length=20,
        choices=SECTION_MODE_CHOICES,
        default=SECTION_MODE_ALL,
        help_text=(
            "Control which article sections require fee quotation. "
            "'All sections' applies to every submission. "
            "'Only selected sections' requires quotation only for the chosen sections. "
            "'All except selected sections' requires quotation for all sections except the chosen ones."
        ),
    )
    
    selected_sections = models.ManyToManyField(
        "submission.Section",
        blank=True,
        related_name="fee_quotation_configs",
        help_text=(
            "Select the sections this rule applies to. "
            "The meaning depends on the 'Section mode' setting above."
        ),
    )
    
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Fee Quotation Configuration"
        verbose_name_plural = "Fee Quotation Configurations"
    
    def __str__(self):
        return f"Fee Quotation Config for {self.journal.code}"
    
    def save(self, *args, **kwargs):
        # Generate webhook secret if not set
        if not self.webhook_secret:
            self.webhook_secret = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)
    
    def requires_quotation_for_section(self, section):
        """
        Check if fee quotation is required for the given section.
        
        Args:
            section: A submission.Section instance, or None
        
        Returns:
            bool: True if quotation is required for this section
        """
        if not self.is_enabled:
            return False
        
        if section is None:
            # If no section specified, default to requiring quotation
            # (the check will happen again when section is selected)
            return self.section_mode == self.SECTION_MODE_ALL
        
        if self.section_mode == self.SECTION_MODE_ALL:
            return True
        
        section_is_selected = self.selected_sections.filter(pk=section.pk).exists()
        
        if self.section_mode == self.SECTION_MODE_INCLUDE:
            # Only require for selected sections
            return section_is_selected
        
        if self.section_mode == self.SECTION_MODE_EXCLUDE:
            # Require for all except selected sections
            return not section_is_selected
        
        return True


class FeeQuotationStatus:
    """Status choices for fee quotations."""
    PENDING = "pending"
    REQUESTED = "requested"
    PRESENTED = "presented"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    ERROR = "error"
    EXPIRED = "expired"
    VOIDED = "voided"
    
    CHOICES = [
        (PENDING, "Pending"),
        (REQUESTED, "Requested"),
        (PRESENTED, "Presented to Author"),
        (ACCEPTED, "Accepted"),
        (DECLINED, "Declined"),
        (ERROR, "Error"),
        (EXPIRED, "Expired"),
        (VOIDED, "Voided"),
    ]


class FeeQuotation(models.Model):
    """
    Tracks individual fee quotation requests and their status.
    """
    article = models.ForeignKey(
        "submission.Article",
        on_delete=models.CASCADE,
        related_name="fee_quotations",
    )
    
    author = models.ForeignKey(
        "core.Account",
        on_delete=models.SET_NULL,
        null=True,
        related_name="fee_quotations",
    )
    
    status = models.CharField(
        max_length=20,
        choices=FeeQuotationStatus.CHOICES,
        default=FeeQuotationStatus.PENDING,
    )
    
    # The quote ID returned by the third-party billing API
    external_quote_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="The quote ID returned by the third-party billing service.",
    )
    
    # The URL for viewing the quotation (built from template + external_quote_id)
    quotation_url = models.URLField(
        max_length=1000,
        blank=True,
        null=True,
        help_text="URL for viewing the quotation (built from template or returned by API).",
    )
    
    # Store the full response from the API for debugging
    api_response = models.JSONField(
        blank=True,
        null=True,
        help_text="The full response from the fee quotation API.",
    )
    
    # Error tracking
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Error message if the quotation request failed.",
    )
    
    # Webhook data
    webhook_received_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When the acceptance webhook was received.",
    )
    
    webhook_payload = models.JSONField(
        blank=True,
        null=True,
        help_text="The payload received from the webhook callback.",
    )
    
    # Timestamps
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When this quotation expires.",
    )
    
    class Meta:
        ordering = ["-created"]
        verbose_name = "Fee Quotation"
        verbose_name_plural = "Fee Quotations"
    
    def __str__(self):
        if self.external_quote_id:
            return f"Quotation {self.external_quote_id} for Article {self.article.pk}"
        return f"Quotation #{self.pk} for Article {self.article.pk}"
    
    @property
    def is_expired(self):
        """Check if the quotation has expired."""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False
    
    @property
    def is_accepted(self):
        """Check if the quotation has been accepted."""
        return self.status == FeeQuotationStatus.ACCEPTED
    
    @property
    def can_proceed(self):
        """Check if the submission can proceed based on quotation status."""
        # If no acceptance is required, always allow
        config = getattr(self.article.journal, 'fee_quotation_config', None)
        if config and not config.require_acceptance:
            return True
        return self.is_accepted
    
    def mark_accepted(self, webhook_payload=None):
        """Mark the quotation as accepted."""
        self.status = FeeQuotationStatus.ACCEPTED
        self.webhook_received_at = timezone.now()
        if webhook_payload:
            self.webhook_payload = webhook_payload
        self.save()
    
    def mark_declined(self, webhook_payload=None):
        """Mark the quotation as declined."""
        self.status = FeeQuotationStatus.DECLINED
        self.webhook_received_at = timezone.now()
        if webhook_payload:
            self.webhook_payload = webhook_payload
        self.save()
    
    def mark_error(self, error_message):
        """Mark the quotation as having an error."""
        self.status = FeeQuotationStatus.ERROR
        self.error_message = error_message
        self.save()
    
    def mark_voided(self, reason=None):
        """Mark the quotation as voided (e.g., due to article changes)."""
        self.status = FeeQuotationStatus.VOIDED
        if reason:
            self.error_message = reason
        self.save()

    @property
    def api_response_pretty(self):
        """Return the API response as pretty-printed JSON for display."""
        if self.api_response:
            return json.dumps(self.api_response, indent=2)
        return ""

    @property
    def webhook_payload_pretty(self):
        """Return the webhook payload as pretty-printed JSON for display."""
        if self.webhook_payload:
            return json.dumps(self.webhook_payload, indent=2)
        return ""
