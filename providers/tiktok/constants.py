"""TikTok archive limits owned by the backend workflow."""

# The first customer-facing metadata import is deliberately bounded.  Later
# batch/rescan work may use separate, explicitly reviewed limits.
INITIAL_PROFILE_IMPORT_POST_LIMIT = 12
