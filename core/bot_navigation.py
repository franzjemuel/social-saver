"""Telegram-native chat navigation for Social Saver.

Actual reply-keyboard buttons remain available beside the chat composer.
Contextual inline buttons appear under navigation/help messages. Mini App URLs
are operator-configured, and the Mini App backend retains auth and ownership.
"""
from urllib.parse import urlsplit

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

LABEL_ARCHIVE = "📚 Open Archive"
LABEL_ADD_PROFILE = "➕ Add Profile"
LABEL_SAVE_LINK = "📥 Save a Link"
LABEL_HELP = "❓ Help"
ACTION_SAVE = "nav:save_link"
ACTION_HELP = "nav:help"

WELCOME_TEXT = (
    "Welcome to Social Saver! 👋\n\n"
    "📥 Paste a supported TikTok or Instagram link here and I'll send the media into this chat.\n\n"
    "📚 Tap Open Archive to browse and preserve your collection, or Add Profile to "
    "manage profile backups.\n\n"
    "The buttons below stay available while you chat. You can still paste links normally."
)

SAVE_HELP_TEXT = (
    "📥 Paste a public TikTok video link (including short TikTok share links) "
    "or a supported Instagram post/Reel link into this chat.\n\n"
    "I’ll find the media and send it here. For TikTok or Instagram profiles, "
    "use the archive workspace. Story, carousel, and profile-link support "
    "varies by platform; not every link is supported yet."
)

HELP_TEXT = (
    "How to use Social Saver\n\n"
    "📥 Quick Save: paste a public TikTok video link or supported Instagram post/Reel link.\n"
    "📚 Open Archive: browse the private collection in the Mini App.\n"
    "➕ Add Profile: open the profile-archive workflow.\n"
    "💾 /archive <Instagram URL>: explicitly preserve supported Instagram media.\n"
    "📋 /myarchive: browse legacy archive entries in chat.\n"
    "💎 /plan: view your current plan.\n\n"
    "TikTok Stories/Highlights, all-profile one-paste retrieval, and photo/carousel "
    "quick-save support are planned, not yet universal."
)

CALLBACK_HELP_TEXT = (
    "Paste a public TikTok video link or supported Instagram media link here. "
    "For profiles, tap Add Profile. Use /help for all options."
)
CALLBACK_SAVE_TEXT = (
    "Paste a TikTok video link or supported Instagram media link into this chat. "
    "Short TikTok share links work too."
)


def mini_app_urls(configured_url: str) -> tuple[str, str]:
    """Return validated, absolute HTTPS homepage and known Add Profile route."""
    if not isinstance(configured_url, str) or not configured_url:
        raise ValueError("Telegram Mini App URL must be configured")
    candidate = configured_url.strip()
    if (candidate != configured_url or len(candidate) > 2048
            or any(ord(char) < 33 or ord(char) > 126 for char in candidate)):
        raise ValueError("Telegram Mini App URL is invalid")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        raise ValueError("Telegram Mini App URL is invalid") from None
    if (
        parsed.scheme != "https" or not parsed.hostname or "." not in parsed.hostname
        or parsed.username is not None or parsed.password is not None or port is not None
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        raise ValueError("Telegram Mini App URL must be an HTTPS origin")
    origin = f"https://{parsed.hostname.lower()}"
    return origin + "/", origin + "/profiles/add"


def chat_reply_keyboard(configured_url: str) -> ReplyKeyboardMarkup:
    """Persistent, touch-friendly Telegram keyboard beneath the chat composer."""
    archive, add_profile = mini_app_urls(configured_url)
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=LABEL_ARCHIVE, web_app=WebAppInfo(url=archive)),
                KeyboardButton(text=LABEL_ADD_PROFILE, web_app=WebAppInfo(url=add_profile)),
            ],
            [KeyboardButton(text=LABEL_SAVE_LINK), KeyboardButton(text=LABEL_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Paste a TikTok or Instagram link…",
    )


def navigation_inline_keyboard(configured_url: str) -> InlineKeyboardMarkup:
    """Real in-message Mini App and guidance buttons, no fake URL actions."""
    archive, add_profile = mini_app_urls(configured_url)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=LABEL_ARCHIVE, web_app=WebAppInfo(url=archive)),
            InlineKeyboardButton(text=LABEL_ADD_PROFILE, web_app=WebAppInfo(url=add_profile)),
        ],
        [
            InlineKeyboardButton(text=LABEL_SAVE_LINK, callback_data=ACTION_SAVE),
            InlineKeyboardButton(text=LABEL_HELP, callback_data=ACTION_HELP),
        ],
    ])


def archive_inline_keyboard(configured_url: str) -> InlineKeyboardMarkup:
    archive, _ = mini_app_urls(configured_url)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=LABEL_ARCHIVE, web_app=WebAppInfo(url=archive)),
    ]])


def add_profile_inline_keyboard(configured_url: str) -> InlineKeyboardMarkup:
    _, add_profile = mini_app_urls(configured_url)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=LABEL_ADD_PROFILE, web_app=WebAppInfo(url=add_profile)),
    ]])


def archive_menu_button(configured_url: str) -> MenuButtonWebApp:
    archive, _ = mini_app_urls(configured_url)
    return MenuButtonWebApp(text="Open Archive", web_app=WebAppInfo(url=archive))
