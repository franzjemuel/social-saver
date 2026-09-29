from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
def entry_text(r):
    who=f"@{r['creator_username']}" if r["creator_username"] else r["platform"].title()
    return f"{who} • {r['media_type']} • {r['asset_count']} item(s)"
def entry_keyboard(e):
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="Download",callback_data=f"ar:get:{e}")],
      [InlineKeyboardButton(text="Delete",callback_data=f"ar:del:{e}")]])
def confirm_keyboard(e):
    return InlineKeyboardMarkup(inline_keyboard=[[
      InlineKeyboardButton(text="Yes, delete",callback_data=f"ar:yes:{e}"),
      InlineKeyboardButton(text="Cancel",callback_data=f"ar:no:{e}")]])
