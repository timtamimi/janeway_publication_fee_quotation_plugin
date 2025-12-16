__copyright__ = "Copyright 2024-2025 Public Library of Science"
__author__ = "Tim Tamimi @ PLOS"
__license__ = "AGPL v3"
__maintainer__ = "Public Library of Science"

from django.db.utils import OperationalError, ProgrammingError

from utils import plugins
from utils.install import update_settings

PLUGIN_NAME = "Publication Fee Quotation"
DISPLAY_NAME = "Fee Quotation"
DESCRIPTION = (
    "Integrates with third-party fee quotation services to provide authors "
    "with publication fee estimates during the submission process."
)
AUTHOR = "Public Library of Science"
VERSION = "1.0"
SHORT_NAME = "publication_fee_quotation"
MANAGER_URL = "fee_quotation_manager"
JANEWAY_VERSION = "1.5.0"

# This is not a workflow plugin - it hooks into the submission start page
IS_WORKFLOW_PLUGIN = False


class PublicationFeeQuotationPlugin(plugins.Plugin):
    plugin_name = PLUGIN_NAME
    display_name = DISPLAY_NAME
    description = DESCRIPTION
    author = AUTHOR
    short_name = SHORT_NAME
    version = VERSION
    janeway_version = JANEWAY_VERSION
    manager_url = MANAGER_URL
    is_workflow_plugin = IS_WORKFLOW_PLUGIN


def install():
    """Install the plugin and create necessary settings."""
    PublicationFeeQuotationPlugin.install()
    update_settings(
        file_path="plugins/publication_fee_quotation/install/settings.json"
    )


def hook_registry():
    """
    Register hooks for the plugin.
    The 'submission_review' hook is rendered on the final review page
    before article submission, when all article data has been collected.
    """
    try:
        return {
            "submission_review": {
                "module": "plugins.publication_fee_quotation.hooks",
                "function": "inject_fee_quotation_ui",
                "name": PLUGIN_NAME,
            },
        }
    except (OperationalError, ProgrammingError):
        # Database not yet created
        return {}
    except Exception:
        return {}

