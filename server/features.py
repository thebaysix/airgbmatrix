"""Feature flags for in-development airgbmatrix features.

Toggle a flag to True/False to turn the feature on/off. All gated code paths
read the flag at the relevant decision point — flipping a flag should change
behavior on the next server restart and the next hook fire.

Once a feature has settled in (or been ruled out), the flag goes away and the
gated code becomes unconditional (kept) or gets deleted (dropped). Search the
repo for the flag name to find every gate.

Keep this file in sync with `s3/features.py` — the S3 path imports its own
copy so it stays self-contained for the eventual CircuitPython deployment.
"""

# Amber-blink the pending column when the session is blocked on a
# permission prompt. Disabled — Claude Code's Notification hook fires
# *after* the user resolves the prompt (not when it appears), so the blink
# would always start post-accept rather than during the wait. See
# "Known limitations" in README. Flip back on if a PermissionRequested-
# style hook lands upstream.
BLINK_ON_PERMISSIONS = False
