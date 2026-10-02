"""Venue preference emoji based on ResearchStudio-Idea PC vs Society classification."""

PREFERENCE_EMOJI = {
    "pc": "\U0001F3C6",      # 🏆
    "society": "\U0001F393",  # 🎓
    "both": "\U0001F947",     # 🥇
}

def get_emoji_for_preference(tier: str | None) -> str:
    """Return emoji string for a preference tier, or '' if None."""
    if tier is None:
        return ""
    return PREFERENCE_EMOJI.get(tier, "")
