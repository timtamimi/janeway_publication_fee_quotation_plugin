__copyright__ = "Copyright 2024-2025 Public Library of Science"
__author__ = "Tim Tamimi @ PLOS"
__license__ = "AGPL v3"
__maintainer__ = "Public Library of Science"

from django.template.loader import render_to_string

from plugins.publication_fee_quotation import models, logic
from utils.logger import get_logger

logger = get_logger(__name__)


def inject_fee_quotation_ui(context):
    """
    Hook function to inject the fee quotation UI into the submission review page.
    
    This hook is called when the 'submission_review' hook is rendered in the
    submit_review.html template, at the end of the submission flow when all
    article data has been collected.
    
    Args:
        context: The template context from the calling template
    
    Returns:
        str: HTML to inject into the page
    """
    request = context.get("request")
    if not request:
        return ""
    
    journal = getattr(request, "journal", None)
    if not journal:
        return ""
    
    # Check if the plugin is configured and enabled for this journal
    try:
        config = journal.fee_quotation_config
        if not config.is_enabled:
            return ""
    except models.FeeQuotationConfiguration.DoesNotExist:
        return ""
    
    # Get the article from context - this is available on the review page
    article = context.get("article")
    if not article:
        logger.debug("No article in context for fee quotation hook")
        return ""
    
    # Check if fee quotation is required for this article's section
    if not logic.is_quotation_required_for_article(article):
        logger.debug(f"Fee quotation not required for article {article.pk} (section: {article.section})")
        return ""
    
    # Get or check for existing quotation
    quotation = logic.get_article_quotation(article)
    
    # Check if the article has been modified since the quotation was created
    # If so, void the old quotation since the article data may have changed
    if quotation and should_void_quotation(article, quotation):
        logic.void_article_quotations(
            article,
            reason="Article was modified after quotation was requested."
        )
        quotation = None  # Reset so the UI shows no active quotation
    
    # Render the fee quotation UI
    hook_context = {
        "request": request,
        "config": config,
        "article": article,
        "quotation": quotation,
        "quotation_required": True,
        "quotation_accepted": quotation.is_accepted if quotation else False,
    }
    
    try:
        return render_to_string(
            "publication_fee_quotation/elements/submission_hook.html",
            hook_context,
            request=request,
        )
    except Exception as e:
        logger.error(f"Error rendering fee quotation hook: {e}")
        return ""


def should_void_quotation(article, quotation):
    """
    Determine if a quotation should be voided based on article modifications.
    
    A quotation should be voided if:
    - The article (or its related objects like authors) was modified after
      the quotation was created
    - The quotation is in an active (non-final) state
    
    Uses Article.fast_last_modified_date() which considers changes to:
    - The article itself
    - Authors (FrozenAuthor)
    - Files
    - Galleys
    
    Args:
        article: The Article instance
        quotation: The FeeQuotation instance
    
    Returns:
        bool: True if the quotation should be voided
    """
    # Only void active quotations (not already accepted, declined, etc.)
    active_statuses = [
        models.FeeQuotationStatus.PENDING,
        models.FeeQuotationStatus.REQUESTED,
        models.FeeQuotationStatus.PRESENTED,
    ]
    
    if quotation.status not in active_statuses:
        return False
    
    # Use fast_last_modified_date() to catch changes to article, authors, files, etc.
    # This is more comprehensive than just checking article.last_modified
    try:
        article_last_modified = article.fast_last_modified_date()
    except Exception:
        # Fallback to basic last_modified if the method fails
        article_last_modified = getattr(article, 'last_modified', None)
    
    if article_last_modified and article_last_modified > quotation.created:
        logger.info(
            f"Article {article.pk} was modified ({article_last_modified}) "
            f"after quotation {quotation.pk} was created ({quotation.created})"
        )
        return True
    
    return False

