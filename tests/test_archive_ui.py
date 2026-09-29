from core.archive_ui import entry_keyboard,confirm_keyboard
def test_callback_payloads_fit_telegram_limit():
    e="00000000-0000-0000-0000-000000000000"
    buttons=[b for row in entry_keyboard(e).inline_keyboard+confirm_keyboard(e).inline_keyboard for b in row]
    assert all(len(b.callback_data.encode())<=64 for b in buttons)
