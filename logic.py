__copyright__ = "Copyright 2024-2025 Public Library of Science"
__author__ = "Tim Tamimi @ PLOS"
__license__ = "AGPL v3"
__maintainer__ = "Public Library of Science"

import json

import requests
from django.urls import reverse

from plugins.janeway_publication_fee_quotation_plugin import models
from utils.logger import get_logger

logger = get_logger(__name__)

# Timeout for API requests (seconds)
API_TIMEOUT = 30


def build_author_identifiers(frozen_author):
    """
    Build the authorIdentifiers array for a frozen author.
    Includes ORCID if available and a unique identifier.
    """
    identifiers = []

    # Add a unique identifier (using the FrozenAuthor pk)
    if frozen_author.pk:
        identifiers.append(
            {
                "type": "OTHER",
                "value": str(frozen_author.pk),
            }
        )

    # Add ORCID if available
    if frozen_author.orcid:
        identifiers.append(
            {
                "type": "ORCID",
                "value": frozen_author.orcid,
            }
        )

    return identifiers


def build_institution_identifiers(organization):
    """
    Build the institutionIdentifiers array for an organization.
    Includes ROR ID and/or Ringgold ID if available.
    """
    identifiers = []

    if not organization:
        return identifiers

    # Add ROR ID if available
    if organization.ror_id:
        identifiers.append(
            {
                "type": "ROR",
                "value": organization.ror_id,
            }
        )

    # Add Ringgold ID if the ringgold_import plugin is installed and
    # the organization has a linked Ringgold identifier
    try:
        ringgold = getattr(organization, "ringgold", None)
        if ringgold and ringgold.ringgold_id:
            identifiers.append(
                {
                    "type": "RINGGOLD",
                    "value": ringgold.ringgold_id,
                }
            )
    except Exception:
        # ringgold_import plugin not installed or other error
        pass

    return identifiers


def build_author_address(affiliation):
    """
    Build the address object for an author based on their primary affiliation.
    Janeway stores limited address data (city and country from organization location).
    """
    address = {
        "address1": "",
        "address2": "",
        "address3": "",
        "city": "",
        "country": "",
        "fax": "",
        "phone": "",
        "phoneExt": "",
        "state": "",
        "zip": "",
    }

    if affiliation and affiliation.organization:
        org = affiliation.organization
        location = org.location
        if location:
            address["city"] = location.name or ""
            if location.country:
                # Use ISO 3166-1 alpha-2 country code (lowercase)
                address["country"] = (
                    location.country.code.lower() if location.country.code else ""
                )

    return address


def build_author_data(frozen_author, article):
    """
    Build the author data dictionary for a single frozen author.

    Args:
        frozen_author: A FrozenAuthor instance
        article: The Article instance (used to determine primary/correspondence author)

    Returns:
        dict: Author data matching the expected payload format
    """
    # Get primary affiliation
    primary_affiliation = frozen_author.primary_affiliation()

    # Get organization from affiliation
    organization = primary_affiliation.organization if primary_affiliation else None

    # Determine if this is the primary (corresponding) author
    # The primary author is the correspondence_author on the article,
    # or if not set, the first author (order=1)
    is_primary = False
    if article.correspondence_author and frozen_author.author:
        is_primary = frozen_author.author == article.correspondence_author
    elif frozen_author.order == 1:
        # If no correspondence author is set, first author is primary
        is_primary = article.correspondence_author is None

    return {
        "address": build_author_address(primary_affiliation),
        "authorIdentifiers": build_author_identifiers(frozen_author),
        "departmentName": primary_affiliation.department if primary_affiliation else "",
        "emailAddress": frozen_author.email or "",
        "firstName": frozen_author.first_name or "",
        "institutionIdentifiers": build_institution_identifiers(organization),
        "institutionName": str(organization) if organization else "",
        "lastName": frozen_author.last_name or "",
        "middleName": frozen_author.middle_name or "",
        "primary": "true" if is_primary else "false",
        "salutation": frozen_author.name_prefix or "",
        "suffix": frozen_author.name_suffix or "",
    }


def build_authors_list(article):
    """
    Build the full authors list for an article.

    Args:
        article: The Article instance

    Returns:
        list: List of author data dictionaries
    """
    authors = []

    # Get all frozen authors for the article, ordered by their order field
    frozen_authors = article.frozenauthor_set.all().order_by("order", "pk")

    for frozen_author in frozen_authors:
        authors.append(build_author_data(frozen_author, article))

    return authors


def get_or_create_quotation(article, author):
    """
    Get an existing quotation for the article or create a new one.
    Returns the most recent non-expired, non-error quotation if available.
    """
    # Look for an existing valid quotation
    existing = (
        models.FeeQuotation.objects.filter(
            article=article,
            author=author,
            status__in=[
                models.FeeQuotationStatus.PENDING,
                models.FeeQuotationStatus.REQUESTED,
                models.FeeQuotationStatus.PRESENTED,
                models.FeeQuotationStatus.ACCEPTED,
            ],
        )
        .order_by("-created")
        .first()
    )

    if existing and not existing.is_expired:
        return existing

    # Create a new quotation
    return models.FeeQuotation.objects.create(
        article=article,
        author=author,
        status=models.FeeQuotationStatus.PENDING,
    )


def render_template(template, context):
    """
    Render a template string with the given context.
    Replaces {{placeholder}} with context values.

    Special handling for complex types (lists, dicts) which are
    serialized as JSON. The placeholder for these should be quoted
    in the template (e.g., "{{authors}}") and will be replaced with
    the JSON representation without surrounding quotes.
    """
    result = template
    for key, value in context.items():
        placeholder = f"{{{{{key}}}}}"

        if isinstance(value, (list, dict)):
            # For complex types, serialize as JSON
            # Handle both quoted ("{{key}}") and unquoted ({{key}}) placeholders
            json_value = json.dumps(value)
            # Replace quoted placeholder first (remove surrounding quotes)
            quoted_placeholder = f'"{placeholder}"'
            if quoted_placeholder in result:
                result = result.replace(quoted_placeholder, json_value)
            else:
                result = result.replace(placeholder, json_value)
        else:
            result = result.replace(placeholder, str(value) if value else "")
    return result


def get_nested_value(data, field_path):
    """
    Extract a value from a nested dictionary using dot notation.

    Args:
        data: The dictionary to extract from
        field_path: The field path (e.g., 'data.quote_id' or 'quote_id')

    Returns:
        The value at the path, or None if not found
    """
    if not field_path or not data:
        return None

    parts = field_path.split(".")
    current = data

    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None

    return current


def build_request_context(article, author, request):
    """
    Build the context dictionary for rendering the request body template.
    """
    callback_url = request.build_absolute_uri(
        reverse(
            "fee_quotation_webhook",
            kwargs={"journal_code": article.journal.code},
        )
    )

    # Get section info
    section_name = ""
    section_id = ""
    if article.section:
        section_name = article.section.name or ""
        section_id = str(article.section.pk)

    # Build authors list
    authors = build_authors_list(article)

    return {
        "article_id": article.pk,
        "article_title": article.title or "",
        "author_email": author.email,
        "author_name": author.full_name(),
        "authors": authors,
        "journal_code": article.journal.code,
        "journal_name": article.journal.name,
        "section_name": section_name,
        "section_id": section_id,
        "callback_url": callback_url,
    }


def build_quotation_url(config, quote_id):
    """
    Build the quotation URL from the template and quote ID.

    Args:
        config: The FeeQuotationConfiguration
        quote_id: The quote ID from the third-party API

    Returns:
        str: The quotation URL, or None if template not configured
    """
    if not config.quotation_url_template or not quote_id:
        return None

    return config.quotation_url_template.replace("{{quote_id}}", str(quote_id))


def request_fee_quotation(article, author, request):
    """
    Request a fee quotation from the third-party API.

    Args:
        article: The Article being submitted
        author: The Account of the submitting author
        request: The Django request object (for building absolute URLs)

    Returns:
        FeeQuotation: The quotation object with updated status
    """
    # Get or create a quotation
    quotation = get_or_create_quotation(article, author)

    # If already successfully requested, return it
    if quotation.status in [
        models.FeeQuotationStatus.PRESENTED,
        models.FeeQuotationStatus.ACCEPTED,
    ]:
        return quotation

    # Get the configuration
    try:
        config = article.journal.fee_quotation_config
    except models.FeeQuotationConfiguration.DoesNotExist:
        quotation.mark_error("Fee quotation not configured for this journal.")
        return quotation

    if not config.is_enabled:
        quotation.mark_error("Fee quotation is not enabled for this journal.")
        return quotation

    if not config.api_url:
        quotation.mark_error("Fee quotation API URL is not configured.")
        return quotation

    # Build the request
    context = build_request_context(article, author, request)

    try:
        # Render the request body
        body_json = render_template(config.request_body_template, context)
        body = json.loads(body_json)
    except json.JSONDecodeError as e:
        quotation.mark_error(f"Invalid request body template: {e}")
        return quotation

    # Parse headers
    headers = {"Content-Type": "application/json"}
    if config.api_headers:
        try:
            custom_headers = json.loads(config.api_headers)
            headers.update(custom_headers)
        except json.JSONDecodeError:
            logger.warning(
                f"Invalid API headers JSON for journal {article.journal.code}"
            )

    # Make the API request
    try:
        logger.info(
            f"Requesting fee quotation from {config.api_url} for article {article.pk}"
        )
        response = requests.post(
            config.api_url,
            json=body,
            headers=headers,
            timeout=API_TIMEOUT,
        )
        response.raise_for_status()

        # Parse the response
        response_data = response.json()
        quotation.api_response = response_data

        # Extract the quote ID from the response
        quote_id = get_nested_value(response_data, config.response_quote_id_field)

        if not quote_id:
            quotation.mark_error(
                f"API response did not contain quote ID in field '{config.response_quote_id_field}'. "
                f"Response: {response_data}"
            )
            return quotation

        quotation.external_quote_id = str(quote_id)

        # Build the quotation URL from the template
        if config.quotation_url_template:
            quotation_url = build_quotation_url(config, quote_id)
            if quotation_url:
                quotation.quotation_url = quotation_url
            else:
                quotation.mark_error("Failed to build quotation URL from template.")
                return quotation
        else:
            # Fallback: try to get URL directly from response
            quotation_url = (
                get_nested_value(response_data, "url")
                or get_nested_value(response_data, "quotation_url")
                or get_nested_value(response_data, "redirect_url")
                or get_nested_value(response_data, "quote_url")
            )
            if quotation_url:
                quotation.quotation_url = quotation_url
            else:
                quotation.mark_error(
                    "No quotation URL template configured and API response did not contain a URL."
                )
                return quotation

        quotation.status = models.FeeQuotationStatus.PRESENTED
        quotation.save()
        logger.info(
            f"Fee quotation {quotation.pk} presented with URL: {quotation.quotation_url}"
        )

    except requests.exceptions.Timeout:
        quotation.mark_error("Fee quotation API request timed out.")
        logger.error(f"Timeout requesting fee quotation for article {article.pk}")

    except requests.exceptions.RequestException as e:
        quotation.mark_error(f"API request failed: {e}")
        logger.error(f"Error requesting fee quotation for article {article.pk}: {e}")

    except json.JSONDecodeError:
        quotation.mark_error("API response was not valid JSON.")
        logger.error(
            f"Invalid JSON response for fee quotation for article {article.pk}"
        )

    return quotation


def is_quotation_required_for_article(article):
    """
    Check if fee quotation is required for the given article based on
    configuration and section conditions.

    Args:
        article: The Article to check

    Returns:
        bool: True if quotation is required for this article
    """
    try:
        config = article.journal.fee_quotation_config
    except models.FeeQuotationConfiguration.DoesNotExist:
        return False

    if not config.is_enabled:
        return False

    # Check section-based conditions
    return config.requires_quotation_for_section(article.section)


def is_quotation_required_for_section(journal, section):
    """
    Check if fee quotation is required for the given section.

    Args:
        journal: The Journal
        section: The Section (can be None)

    Returns:
        bool: True if quotation is required for this section
    """
    try:
        config = journal.fee_quotation_config
    except models.FeeQuotationConfiguration.DoesNotExist:
        return False

    if not config.is_enabled:
        return False

    return config.requires_quotation_for_section(section)


def check_quotation_accepted(article):
    """
    Check if the article has an accepted fee quotation.

    Args:
        article: The Article to check

    Returns:
        bool: True if the quotation is accepted or not required
    """
    # First check if quotation is even required for this article
    if not is_quotation_required_for_article(article):
        return True

    try:
        config = article.journal.fee_quotation_config
    except models.FeeQuotationConfiguration.DoesNotExist:
        # No config means quotation is not required
        return True

    if not config.require_acceptance:
        return True

    # Check for an accepted quotation
    return models.FeeQuotation.objects.filter(
        article=article,
        status=models.FeeQuotationStatus.ACCEPTED,
    ).exists()


def get_article_quotation(article):
    """
    Get the current/latest quotation for an article.

    Args:
        article: The Article

    Returns:
        FeeQuotation or None
    """
    return (
        models.FeeQuotation.objects.filter(
            article=article,
        )
        .order_by("-created")
        .first()
    )


def void_article_quotations(article, reason=None):
    """
    Void all active (non-final) quotations for an article.

    This should be called when the article submission is updated in ways
    that could affect the fee calculation (e.g., authors changed, section changed).

    Args:
        article: The Article whose quotations should be voided
        reason: Optional reason for voiding (stored in error_message)

    Returns:
        int: Number of quotations voided
    """
    # Statuses that should be voided (active, non-final quotations)
    active_statuses = [
        models.FeeQuotationStatus.PENDING,
        models.FeeQuotationStatus.REQUESTED,
        models.FeeQuotationStatus.PRESENTED,
    ]

    active_quotations = models.FeeQuotation.objects.filter(
        article=article,
        status__in=active_statuses,
    )

    count = 0
    for quotation in active_quotations:
        quotation.mark_voided(reason=reason)
        count += 1
        logger.info(f"Voided quotation {quotation.pk} for article {article.pk}")

    return count
