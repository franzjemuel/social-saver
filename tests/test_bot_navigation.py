"""Real Telegram bot chat-button contract for the published Social Saver Mini App.

This is a navigation-only increment. No provider, queue, billing, or archive
worker code is changed by these buttons.
"""
import inspect

import pytest
from aiogram.types import InlineKeyboardMarkup, MenuButtonWebApp, ReplyKeyboardMarkup

from core.bot_navigation import (
    ACTION_HELP, ACTION_SAVE, CALLBACK_HELP_TEXT, CALLBACK_SAVE_TEXT,
    HELP_TEXT, LABEL_ADD_PROFILE, LABEL_ARCHIVE, LABEL_HELP, LABEL_SAVE_LINK,
    SAVE_HELP_TEXT, WELCOME_TEXT, add_profile_inline_keyboard,
    archive_inline_keyboard, archive_menu_button, chat_reply_keyboard,
    mini_app_urls, navigation_inline_keyboard,
)

APP = "https://social-story-saver.lovable.app"
ARCHIVE = APP + "/"
ADD = APP + "/profiles/add"


def test_verifies_known_published_archive_and_add_profile_routes():
    assert mini_app_urls(APP) == (ARCHIVE, ADD)
    assert mini_app_urls(APP + "/") == (ARCHIVE, ADD)
    assert mini_app_urls("https://my-custom-host.example") == (
        "https://my-custom-host.example/",
        "https://my-custom-host.example/profiles/add",
    )


@pytest.mark.parametrize("url", [
    "", None, "http://social-story-saver.lovable.app",
    "https://user@social-story-saver.lovable.app",
    "https://social-story-saver.lovable.app:8080",
    "https://social-story-saver.lovable.app/?id=123",
    "https://social-story-saver.lovable.app/#frag",
    "https://social-story-saver.lovable.app/unknown",
    "https://social-story-saver.lovable.app.evil.org/@",
    "https://social-story-saver.lovable.app\nmalicious",
    "https://localhost",
    "https://127.0.0.1:5000",
])
def test_rejects_malformed_or_non_origin_app_urls(url):
    with pytest.raises(ValueError):
        mini_app_urls(url)


def test_persistent_chat_keyboard_has_real_webapp_and_guidance_buttons():
    markup = chat_reply_keyboard(APP)
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert markup.resize_keyboard is True
    assert markup.is_persistent is True
    assert len(markup.keyboard) == 2

    archive, add = markup.keyboard[0]
    assert archive.text == LABEL_ARCHIVE
    assert archive.web_app.url == ARCHIVE
    assert add.text == LABEL_ADD_PROFILE
    assert add.web_app.url == ADD

    save, help_button = markup.keyboard[1]
    assert save.text == LABEL_SAVE_LINK and save.web_app is None
    assert help_button.text == LABEL_HELP and help_button.web_app is None
    assert "Paste a TikTok or Instagram link" in markup.input_field_placeholder


def test_inline_actions_open_actual_webapps_and_callbacks_are_static():
    markup = navigation_inline_keyboard(APP)
    assert isinstance(markup, InlineKeyboardMarkup)
    archive, add = markup.inline_keyboard[0]
    assert archive.web_app.url == ARCHIVE
    assert add.web_app.url == ADD
    save, help_button = markup.inline_keyboard[1]
    assert save.callback_data == ACTION_SAVE
    assert help_button.callback_data == ACTION_HELP
    assert all(button.url is None for row in markup.inline_keyboard for button in row)
    # No user, chat, profile, archive or storage identifiers in callback data.
    assert all(
        button.callback_data in {ACTION_SAVE, ACTION_HELP}
        for row in markup.inline_keyboard for button in row
        if button.callback_data is not None
    )


def test_individual_action_buttons_are_real_mini_app_launches():
    archive = archive_inline_keyboard(APP)
    add = add_profile_inline_keyboard(APP)
    assert archive.inline_keyboard[0][0].web_app.url == ARCHIVE
    assert add.inline_keyboard[0][0].web_app.url == ADD
    menu = archive_menu_button(APP)
    assert isinstance(menu, MenuButtonWebApp)
    assert menu.web_app.url == ARCHIVE
    assert menu.text == "Open Archive"


def test_navigation_guidance_does_not_overclaim_future_support():
    assert "not every link is supported yet" in SAVE_HELP_TEXT
    assert "TikTok Stories/Highlights" in HELP_TEXT
    assert "TikTok" in WELCOME_TEXT and "Instagram" in WELCOME_TEXT
    assert len(CALLBACK_HELP_TEXT) <= 200
    assert len(CALLBACK_SAVE_TEXT) <= 200


def test_bot_routes_all_actual_buttons_before_general_link_handler():
    from apps.bot.main import main
    source = inspect.getsource(main)
    assert "@dp.message(Command(\"start\"))" in source
    assert "@dp.message(Command(\"menu\"))" in source
    assert "reply_markup=chat_reply_keyboard(settings.telegram_mini_app_url)" in source
    assert "await bot.set_chat_menu_button(" in source
    assert "@dp.message(Command(\"app\"))" in source
    assert "@dp.message(Command(\"add\"))" in source
    assert "@dp.message(F.text == LABEL_SAVE_LINK)" in source
    assert "@dp.message(F.text == LABEL_HELP)" in source
    assert "F.data.in_({ACTION_SAVE, ACTION_HELP})" in source
    assert source.index("@dp.message(F.text == LABEL_SAVE_LINK)") < source.index("@dp.message(F.text)")

    # Preserve the existing one-link quick-save and explicit archive flows.
    assert "normalize_tiktok_quick_save_url(text)" in source
    assert '"tiktok_quick_save"' in source
    assert "normalize_instagram_url(parts[1].strip())" in source
    assert '"resolve_media"' in source


def test_read_only_navigation_callbacks_do_not_mutate_archive_or_enqueue_jobs():
    from apps.bot.main import main
    source = inspect.getsource(main)
    segment = source[source.index("async def navigation_button("):source.index("@dp.message(Command(\"stories\"))")]
    assert "callback.answer" in segment
    assert "queue.send" not in segment
    assert "repo." not in segment
    assert "bot.send_message" not in segment


def test_private_only_webapp_navigation_and_legacy_archive_contract_stay_separate():
    from apps.bot.main import main
    source = inspect.getsource(main)
    assert 'message.chat.type == "private"' in source
    assert 'if not in_private_chat(message):' in source
    begin = source.index('async def archive(message:Message):')
    end = source.index('async def myarchive(', begin)
    archive_command = source[begin:end]
    assert 'await send_archive_entry(message)' in archive_command
    assert 'normalize_instagram_url(parts[1].strip())' in archive_command
