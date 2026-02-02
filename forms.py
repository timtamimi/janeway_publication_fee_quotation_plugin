__copyright__ = "Copyright 2024-2025 Public Library of Science"
__author__ = "Tim Tamimi @ PLOS"
__license__ = "AGPL v3"
__maintainer__ = "Public Library of Science"

import json

from django import forms
from django.core.exceptions import ValidationError

from plugins.janeway_publication_fee_quotation_plugin import models


class FeeQuotationConfigurationForm(forms.ModelForm):
    """Form for configuring the fee quotation service."""

    class Meta:
        model = models.FeeQuotationConfiguration
        fields = [
            "is_enabled",
            "section_mode",
            "selected_sections",
            "api_url",
            "request_body_template",
            "api_headers",
            "response_quote_id_field",
            "quotation_url_template",
            "button_text",
            "instructions_text",
            "require_acceptance",
        ]
        widgets = {
            "request_body_template": forms.Textarea(
                attrs={
                    "rows": 12,
                    "class": "code-editor",
                    "placeholder": """{
    "article_id": "{{article_id}}",
    "article_title": "{{article_title}}",
    "author_email": "{{author_email}}",
    "callback_url": "{{callback_url}}"
}""",
                }
            ),
            "api_headers": forms.Textarea(
                attrs={
                    "rows": 4,
                    "class": "code-editor",
                    "placeholder": '{"Authorization": "Bearer YOUR_API_KEY"}',
                }
            ),
            "instructions_text": forms.Textarea(
                attrs={
                    "rows": 4,
                }
            ),
            "api_url": forms.URLInput(
                attrs={
                    "placeholder": "https://api.example.com/quotation",
                }
            ),
            "quotation_url_template": forms.TextInput(
                attrs={
                    "placeholder": "https://billing.example.com/quote/{{quote_id}}",
                }
            ),
            "response_quote_id_field": forms.TextInput(
                attrs={
                    "placeholder": "quote_id",
                }
            ),
            "selected_sections": forms.CheckboxSelectMultiple(),
        }
        help_texts = {
            "is_enabled": (
                "Enable the fee quotation feature for this journal. "
                "When enabled, authors will be prompted to request and accept "
                "a fee quotation before submitting."
            ),
            "require_acceptance": (
                "If enabled, authors must accept the fee quotation before "
                "they can complete their submission. If disabled, the quotation "
                "is shown but acceptance is optional."
            ),
            "section_mode": (
                "Choose how to apply fee quotation based on article sections."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter sections to only show those belonging to the journal
        if self.instance and self.instance.journal_id:
            from submission.models import Section

            self.fields["selected_sections"].queryset = Section.objects.filter(
                journal=self.instance.journal
            ).order_by("sequence", "name")

    def clean_request_body_template(self):
        """Validate that the request body template is valid JSON."""
        template = self.cleaned_data.get("request_body_template", "")
        if template:
            # Replace placeholders with dummy values for validation
            test_json = template
            placeholders = [
                "{{article_id}}",
                "{{article_title}}",
                "{{author_email}}",
                "{{author_name}}",
                "{{journal_code}}",
                "{{journal_name}}",
                "{{section_name}}",
                "{{section_id}}",
                "{{callback_url}}",
                "{{quotation_id}}",
            ]
            for placeholder in placeholders:
                test_json = test_json.replace(placeholder, "test_value")

            try:
                json.loads(test_json)
            except json.JSONDecodeError as e:
                raise ValidationError(
                    f"Invalid JSON template: {e}. "
                    "Please ensure the template is valid JSON with placeholders."
                )
        return template

    def clean_api_headers(self):
        """Validate that the API headers are valid JSON."""
        headers = self.cleaned_data.get("api_headers", "")
        if headers:
            try:
                parsed = json.loads(headers)
                if not isinstance(parsed, dict):
                    raise ValidationError(
                        "API headers must be a JSON object (dictionary)."
                    )
            except json.JSONDecodeError as e:
                raise ValidationError(f"Invalid JSON for API headers: {e}")
        return headers
