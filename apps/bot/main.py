import asyncio, logging, time
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, LabeledPrice
from aiogram.exceptions import TelegramAPIError
from core.config import settings
from core.database import Database
from core.queue import JobQueue
from core.repository import Repository
from core.archive_ui import entry_text, entry_keyboard, confirm_keyboard
from core.storage import R2Storage
from core.entitlements import EntitlementService
from core.payments import PaymentService, SUBSCRIPTION_PERIOD
from core.watches import WatchService
from core.rate_limits import AbuseLimiter
from core.observability import init_observability
from core.health import system_health
from core.readiness import start_readiness_server
from providers.instagram.watch import InstagramWatchProvider
from providers.instagram.provider import normalize_instagram_url
from providers.base import UnsupportedUrl
from providers.tiktok.quick_save import is_tiktok_link, normalize_tiktok_quick_save_url
from providers.instagram.apify_stories import normalize_public_profile
from core.admin import parse_admin_telegram_ids
from core.bot_navigation import (
    ACTION_HELP, ACTION_SAVE, CALLBACK_HELP_TEXT, CALLBACK_SAVE_TEXT,
    HELP_TEXT, LABEL_ADD_PROFILE, LABEL_ARCHIVE, LABEL_HELP, LABEL_SAVE_LINK,
    SAVE_HELP_TEXT, WELCOME_TEXT,
    add_profile_inline_keyboard, archive_inline_keyboard, archive_menu_button,
    chat_reply_keyboard, navigation_inline_keyboard,
)

async def main():
    init_observability("telegram-bot")
    db=Database(settings.database_url); await db.connect()

    async def ready_probe():
        try:
            ok = await db.pool.fetchval("select 1")
            return bool(ok == 1), "database_ready"
        except Exception:
            return False, "database_unavailable"

    readiness_server = await start_readiness_server(ready_probe, port=settings.port)
    repo=Repository(db.pool)
    entitlements=EntitlementService(db.pool)
    payments=PaymentService(db.pool)
    watches=WatchService(db.pool)
    abuse=AbuseLimiter()
    admin_telegram_ids=parse_admin_telegram_ids(settings.admin_telegram_user_ids)
    queue=JobQueue(db.pool,settings.queue_name,settings.queue_visibility_seconds)
    bot=Bot(settings.telegram_bot_token)
    dp=Dispatcher()

    # Keep the global Telegram menu pointed at the published Mini App.
    # This is best-effort: Telegram API downtime must not disable Quick Save.
    try:
        await bot.set_chat_menu_button(
            menu_button=archive_menu_button(settings.telegram_mini_app_url)
        )
    except TelegramAPIError as exc:
        logging.getLogger(__name__).warning(
            "telegram_archive_menu_configuration_failed type=%s",
            type(exc).__name__,
        )

    def in_private_chat(message: Message) -> bool:
        return bool(message.chat and message.chat.type == "private")

    async def send_archive_entry(message: Message) -> None:
        if not in_private_chat(message):
            await message.answer("Open a private chat with @socialsaverapp_bot to use your archive.")
            return
        await message.answer(
            "📚 Your private archive is in the Mini App.",
            reply_markup=archive_inline_keyboard(settings.telegram_mini_app_url),
        )

    async def send_add_profile_entry(message: Message) -> None:
        if not in_private_chat(message):
            await message.answer("Open a private chat with @socialsaverapp_bot to add a profile.")
            return
        await message.answer(
            "➕ Open the Mini App to add a TikTok profile to your private archive.",
            reply_markup=add_profile_inline_keyboard(settings.telegram_mini_app_url),
        )

    async def send_help(message: Message) -> None:
        await message.answer(
            HELP_TEXT,
            reply_markup=(
                navigation_inline_keyboard(settings.telegram_mini_app_url)
                if in_private_chat(message) else None
            ),
        )

    async def user_id(message):
        u=message.from_user
        return await repo.get_or_create_telegram_user(u.id,u.username,u.first_name)

    async def velocity_ok(message,uid):
        if is_admin(message): return True
        plan=await entitlements.plan_for(uid)
        result=await abuse.check(uid,plan)
        if result.allowed: return True
        await message.answer(f"Too many requests at once. Try again in about {max(1,int(result.reset-time.time()))} seconds.")
        return False

    def is_admin(message):
        return bool(message.from_user and message.from_user.id in admin_telegram_ids)

    @dp.message(Command("start"))
    @dp.message(Command("menu"))
    async def start(message:Message):
        await user_id(message)
        if not in_private_chat(message):
            await message.answer(
                "👋 Social Saver delivers supported TikTok and Instagram media. "
                "Open my private chat to use the archive and navigation buttons."
            )
            return
        await message.answer(
            WELCOME_TEXT,
            reply_markup=chat_reply_keyboard(settings.telegram_mini_app_url),
        )

    @dp.message(Command("app"))
    async def open_app(message:Message):
        await send_archive_entry(message)

    @dp.message(Command("add"))
    async def add_profile(message:Message):
        await send_add_profile_entry(message)

    @dp.message(Command("help"))
    @dp.message(F.text == LABEL_HELP)
    async def help_menu(message:Message):
        await send_help(message)

    @dp.message(F.text == LABEL_SAVE_LINK)
    async def prompt_save_link(message:Message):
        await message.answer(SAVE_HELP_TEXT)

    # Web App reply-keyboard buttons normally open the app without emitting
    # text. The text handlers are graceful fallbacks for legacy clients.
    @dp.message(F.text == LABEL_ARCHIVE)
    async def archive_button_fallback(message:Message):
        await send_archive_entry(message)

    @dp.message(F.text == LABEL_ADD_PROFILE)
    async def add_button_fallback(message:Message):
        await send_add_profile_entry(message)

    @dp.callback_query(F.data.in_({ACTION_SAVE, ACTION_HELP}))
    async def navigation_button(callback:CallbackQuery):
        # Read-only, idempotent callbacks: repeated taps never enqueue jobs
        # or mutate account/archive state.
        if callback.data == ACTION_SAVE:
            await callback.answer(CALLBACK_SAVE_TEXT, show_alert=True)
        else:
            await callback.answer(CALLBACK_HELP_TEXT, show_alert=True)

    @dp.message(Command("stories"))
    async def stories(message:Message):
        parts=(message.text or "").split(maxsplit=1)
        if len(parts)!=2:
            await message.answer("Usage: /stories @public_username"); return
        try: target=normalize_public_profile(parts[1])
        except UnsupportedUrl as exc: await message.answer(str(exc)); return
        uid=await user_id(message)
        if not await velocity_ok(message,uid): return
        jid=await repo.create_job(uid,message.chat.id,"resolve_stories",{"target":target})
        await queue.send(str(jid))
        await message.answer(f"🔎 Finding active public Stories for @{target}...")

    @dp.message(Command("status"))
    async def status(message:Message):
        h=await system_health(db.pool,settings.queue_name)
        await message.answer(f"Queue: {h['queue']['length']} pending; oldest {h['queue']['oldest_age_seconds'] or 0}s\nWorkers: {h['workers']['healthy']}/{h['workers']['total']} healthy\nWatch errors: {h['watch_errors']}")

    @dp.message(Command("id"))
    async def telegram_id(message:Message):
        await message.answer(f"Your Telegram user ID is: {message.from_user.id}")

    @dp.message(Command("plan"))
    async def plan(message:Message):
        uid=await user_id(message); code=await entitlements.plan_for(uid)
        _,limit=await entitlements.int_feature(uid,"archive_bytes"); used=await entitlements.archive_usage(uid)
        if is_admin(message):
            await message.answer(f"Plan: Admin\nDownloads: Unlimited\nWatch slots: Unlimited\nArchive: Unlimited ({used/(1024**3):.2f} GB used)\nLive recording: Unlimited")
        else:
            await message.answer(f"Plan: {code.title()}\nArchive: {used/(1024**3):.2f} / {limit/(1024**3):.2f} GB")

    @dp.message(Command("upgrade"))
    async def upgrade(message:Message):
        offers=await payments.offers()
        await message.answer("\n".join(["Choose a 30-day recurring plan:"]+[f"/buy_{o.code} — {o.name}: {o.stars} Stars" for o in offers]))

    @dp.message(F.text.startswith("/buy_"))
    async def buy(message:Message):
        code=(message.text or "")[5:].strip().split("@")[0]; offer=await payments.offer(code)
        if not offer: await message.answer("That plan is not available."); return
        uid=await user_id(message)
        await bot.send_invoice(chat_id=message.chat.id,title=f"Social Saver {offer.name}",
          description=f"{offer.name} plan, recurring every 30 days.",payload=payments.payload(uid,offer.code),
          currency="XTR",prices=[LabeledPrice(label=offer.name,amount=offer.stars)],provider_token="",
          subscription_period=SUBSCRIPTION_PERIOD,start_parameter=f"upgrade-{offer.code}")

    @dp.pre_checkout_query()
    async def checkout(query):
        try:
            payload_uid,code=payments.parse_payload(query.invoice_payload); offer=await payments.offer(code)
            u=query.from_user; uid=await repo.get_or_create_telegram_user(u.id,u.username,u.first_name)
            ok=str(uid)==payload_uid and offer is not None and query.currency=="XTR" and query.total_amount==offer.stars
            await query.answer(ok=ok,error_message=None if ok else "This subscription offer is no longer valid.")
        except Exception: await query.answer(ok=False,error_message="This subscription could not be validated.")

    @dp.message(F.successful_payment)
    async def paid(message:Message):
        sp=message.successful_payment
        try:
            payload_uid,code=payments.parse_payload(sp.invoice_payload); uid=await user_id(message); offer=await payments.offer(code)
            if str(uid)!=payload_uid or not offer or sp.currency!="XTR" or sp.total_amount!=offer.stars:
                await message.answer("Payment received, but activation needs support. Use /paysupport."); return
            if await payments.activate(uid,code,sp):
                await message.answer(f"✅ {offer.name} is active. Use /plan to see your limits.")
        except Exception: await message.answer("Payment received, but activation needs support. Use /paysupport.")

    @dp.message(Command("subscription"))
    async def subscription(message:Message):
        uid=await user_id(message); sub=await payments.active_subscription(uid)
        if not sub: await message.answer("You do not have an active paid subscription."); return
        end=sub["current_period_end"].strftime("%Y-%m-%d %H:%M UTC") if sub["current_period_end"] else "unknown"
        renewal="off" if sub["cancel_at_period_end"] else "on"
        await message.answer(f"Plan: {sub['plan_code'].title()}\nPaid through: {end}\nAuto-renewal: {renewal}")

    @dp.message(Command("cancelplan"))
    async def cancelplan(message:Message):
        uid=await user_id(message); sub=await payments.active_subscription(uid)
        if not sub: await message.answer("You do not have an active paid subscription."); return
        if sub["cancel_at_period_end"]: await message.answer("Auto-renewal is already canceled."); return
        charge=sub["initial_charge_id"] or sub["provider_charge_id"]
        try: await bot.edit_user_star_subscription(user_id=message.from_user.id,telegram_payment_charge_id=charge,is_canceled=True)
        except Exception: await message.answer("Cancellation could not be completed automatically. Use /paysupport."); return
        await payments.mark_cancel_at_period_end(uid,sub["id"])
        await message.answer("Auto-renewal is canceled. Paid access remains through the current period.")

    @dp.message(Command("paysupport"))
    async def paysupport(message:Message):
        await message.answer("Send the approximate purchase time, plan name, and what went wrong. Do not send passwords or payment credentials.")

    async def add_watch(message:Message,mode):
        parts=(message.text or "").split(maxsplit=1)
        if len(parts)!=2: await message.answer("Usage: /watch @instagram_username"); return
        uid=await user_id(message)
        if not await velocity_ok(message,uid): return
        _,limit=await entitlements.int_feature(uid,"watch_slots")
        if not is_admin(message) and await watches.count_active(uid)>=limit: await message.answer(f"Your plan allows {limit} active watch(es)."); return
        try:
            if mode=="stories":
                display=normalize_public_profile(parts[1]); key=display
            else:
                key,display=await InstagramWatchProvider(db.pool).resolve_target(parts[1])
        except Exception: await message.answer("I couldn't resolve that public Instagram profile right now."); return
        interval=300 if mode in ("stories","both") else 900
        await watches.create(uid,"instagram",key,display,mode,interval)
        await message.answer(f"👀 Watching @{display} for {mode}. The first poll establishes a baseline.")

    @dp.message(Command("watch"))
    async def watch(message:Message): await add_watch(message,"posts")
    @dp.message(Command("watchstories"))
    async def watchstories(message:Message): await add_watch(message,"stories")

    @dp.message(Command("unwatchstories"))
    async def unwatchstories(message:Message):
        parts=(message.text or "").split(maxsplit=1)
        if len(parts)!=2: await message.answer("Usage: /unwatchstories @username"); return
        username=parts[1].strip().lstrip("@").lower()
        uid=await user_id(message)
        rows=await watches.list(uid)
        removed=0
        for row in rows:
            if row["content_mode"] in ("stories","both") and row["target_display"].lower()==username:
                if await watches.remove(uid,row["id"]): removed+=1
        await message.answer("Story watch removed." if removed else "Story watch not found.")
    @dp.message(Command("watchboth"))
    async def watchboth(message:Message): await add_watch(message,"both")

    @dp.message(Command("savefriend"))
    async def savefriend(message:Message):
        parts=(message.text or "").split(maxsplit=1)
        if len(parts)!=2:
            await message.answer("Usage: /savefriend @instagram_username"); return
        uid=await user_id(message)
        if not await velocity_ok(message,uid): return
        _,watch_limit=await entitlements.int_feature(uid,"watch_slots")
        if not is_admin(message) and await watches.count_active(uid)>=watch_limit:
            await message.answer(f"Your plan allows {watch_limit} active Saved Friend/watch slot(s)."); return
        archive=await entitlements.authorize_archive(uid)
        if not is_admin(message) and not archive.allowed:
            await message.answer("Saved Friends needs archive access on your plan because new Stories are saved automatically."); return
        try: key,display=await InstagramWatchProvider(db.pool).resolve_target(parts[1])
        except Exception:
            await message.answer("I couldn't resolve that Instagram profile right now."); return
        await watches.save_friend(uid,"instagram",key,display,60)
        await message.answer(f"💾 Saved @{display}. New Stories will be checked about every minute, archived automatically, and sent here.")

    @dp.message(Command("friends"))
    async def friends(message:Message):
        rows=await watches.list_saved_friends(await user_id(message))
        if not rows:
            await message.answer("No Saved Friends yet. Add one with /savefriend @username"); return
        await message.answer("Saved Friends:\n"+"\n".join(
          f"@{r['target_display']} · {r['status']} · ~{r['poll_interval_seconds']}s" for r in rows))

    @dp.message(Command("removefriend"))
    async def removefriend(message:Message):
        parts=(message.text or "").split(maxsplit=1)
        if len(parts)!=2:
            await message.answer("Usage: /removefriend @username"); return
        removed=await watches.remove_saved_friend(await user_id(message),parts[1])
        await message.answer("Saved Friend removed." if removed else "Saved Friend not found.")

    @dp.message(Command("watches"))
    async def list_watches(message:Message):
        rows=await watches.list(await user_id(message))
        if not rows: await message.answer("You are not watching any profiles."); return
        await message.answer("\n".join(f"{r['id']}  @{r['target_display']}  {r['content_mode']}  {r['status']}" for r in rows))

    @dp.message(Command("unwatch"))
    async def unwatch(message:Message):
        parts=(message.text or "").split(maxsplit=1)
        if len(parts)!=2: await message.answer("Usage: /unwatch <watch-id>"); return
        removed=await watches.remove(await user_id(message),parts[1].strip())
        await message.answer("Watch removed." if removed else "Watch not found.")

    @dp.message(Command("archive"))
    async def archive(message:Message):
        parts=(message.text or "").split(maxsplit=1)
        # /archive retains its existing URL-archive behavior, while a bare
        # /archive opens the Mini App instead of exposing a confusing error.
        if len(parts)!=2:
            await send_archive_entry(message)
            return
        try: canonical=normalize_instagram_url(parts[1].strip())
        except UnsupportedUrl as exc: await message.answer(f"Unsupported link: {exc}"); return
        uid=await user_id(message)
        if not await velocity_ok(message,uid): return
        decision=await entitlements.authorize_archive(uid)
        if not is_admin(message) and not decision.allowed: await message.answer("Permanent archive is not included on your plan."); return
        jid=await repo.create_job(uid,message.chat.id,"resolve_media",{"url":canonical,"archive":True}); await queue.send(str(jid))
        await message.answer("📦 Saving media and adding it to your archive...")

    @dp.message(Command("myarchive"))
    async def myarchive(message:Message):
        uid=await user_id(message); rows=await repo.list_archive_entries(uid,10)
        if not rows: await message.answer("Your archive is empty."); return
        for row in rows: await message.answer(entry_text(row),reply_markup=entry_keyboard(str(row["id"])))

    @dp.callback_query(F.data.startswith("ar:get:"))
    async def archive_get(callback:CallbackQuery):
        uid=await repo.get_or_create_telegram_user(callback.from_user.id,callback.from_user.username,callback.from_user.first_name)
        assets=await repo.list_archive_assets(uid,callback.data.split(":",2)[2])
        if not assets: await callback.answer("Archive item not found.",show_alert=True); return
        storage=R2Storage(settings.r2_account_id,settings.r2_access_key_id,settings.r2_secret_access_key,settings.r2_bucket,900)
        links=[f"Item {a['position']+1}: {await storage.presigned_get(a['storage_key'],900)}" for a in assets]
        await callback.message.answer("Secure link(s), valid for 15 minutes:\n"+"\n".join(links),disable_web_page_preview=True); await callback.answer()

    @dp.callback_query(F.data.startswith("ar:del:"))
    async def archive_del(callback:CallbackQuery):
        e=callback.data.split(":",2)[2]; await callback.message.answer("Delete this item?",reply_markup=confirm_keyboard(e)); await callback.answer()

    @dp.callback_query(F.data.startswith("ar:no:"))
    async def archive_no(callback:CallbackQuery): await callback.message.edit_text("Deletion canceled."); await callback.answer()

    @dp.callback_query(F.data.startswith("ar:yes:"))
    async def archive_yes(callback:CallbackQuery):
        e=callback.data.split(":",2)[2]
        uid=await repo.get_or_create_telegram_user(callback.from_user.id,callback.from_user.username,callback.from_user.first_name)
        orphaned=await repo.soft_delete_archive_entry(uid,e)
        if orphaned is None: await callback.answer("Archive item not found.",show_alert=True); return
        # Physical deletion is intentionally worker-only. The bot hides the tenant-owned
        # archive row immediately, then queues a purge that rechecks shared references.
        jid=await repo.create_job(uid,callback.message.chat.id,"purge_archive",{"archive_entry_id":e})
        await queue.send(str(jid))
        await callback.message.edit_text("Deleted from your archive. Storage cleanup is queued."); await callback.answer()

    @dp.message(Command("lives"))
    async def lives(message:Message):
        uid=await user_id(message)
        rows=await db.pool.fetch("""select id,status,recorded_seconds,finalize_status,final_storage_key,created_at
          from live_sessions where user_id=$1 order by created_at desc limit 10""",uid)
        if not rows: await message.answer("No Live recordings yet."); return
        storage=R2Storage(settings.r2_account_id,settings.r2_access_key_id,settings.r2_secret_access_key,settings.r2_bucket,settings.r2_presign_seconds)
        lines=[]
        for r in rows:
            mins=max(1,round(int(r["recorded_seconds"] or 0)/60))
            if r["finalize_status"]=="completed" and r["final_storage_key"]:
                url=await storage.presigned_get(r["final_storage_key"],settings.archive_presign_seconds)
                lines.append(f"{r['created_at']:%Y-%m-%d} · {mins} min · ready\n{url}")
            else: lines.append(f"{r['created_at']:%Y-%m-%d} · {mins} min · {r['finalize_status']}")
        await message.answer("\n\n".join(lines))

    @dp.message(F.text)
    async def media_link(message:Message):
        text=(message.text or "").strip()
        if not text.startswith(("http://","https://")): return
        if is_tiktok_link(text):
            try: canonical = normalize_tiktok_quick_save_url(text)
            except UnsupportedUrl as exc:
                await message.answer(str(exc)); return
            uid = await user_id(message)
            if not await velocity_ok(message, uid): return
            jid = await repo.create_job(uid, message.chat.id, "tiktok_quick_save", {"url":canonical})
            await queue.send(str(jid))
            await message.answer("🔎 Finding TikTok video...")
            return
        try: canonical=normalize_instagram_url(text)
        except UnsupportedUrl as exc: await message.answer(f"Unsupported link: {exc}"); return
        uid=await user_id(message)
        if not await velocity_ok(message,uid): return
        jid=await repo.create_job(uid,message.chat.id,"resolve_media",{"url":canonical}); await queue.send(str(jid))
        await message.answer("🔎 Finding media...")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await db.close()

if __name__=="__main__":
    asyncio.run(main())
