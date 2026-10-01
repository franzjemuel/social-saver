import asyncio, time
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, LabeledPrice
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
from providers.instagram.apify_stories import normalize_public_profile

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
    queue=JobQueue(db.pool,settings.queue_name,settings.queue_visibility_seconds)
    bot=Bot(settings.telegram_bot_token)
    dp=Dispatcher()

    async def user_id(message):
        u=message.from_user
        return await repo.get_or_create_telegram_user(u.id,u.username,u.first_name)

    async def velocity_ok(message,uid):
        plan=await entitlements.plan_for(uid)
        result=await abuse.check(uid,plan)
        if result.allowed: return True
        await message.answer(f"Too many requests at once. Try again in about {max(1,int(result.reset-time.time()))} seconds.")
        return False

    @dp.message(Command("start"))
    async def start(message:Message):
        await user_id(message)
        await message.answer("Welcome. Send a supported Instagram link, or use /stories, /savefriend, /friends, /watch, /archive, /plan, or /upgrade.")

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

    @dp.message(Command("plan"))
    async def plan(message:Message):
        uid=await user_id(message); code=await entitlements.plan_for(uid)
        _,limit=await entitlements.int_feature(uid,"archive_bytes"); used=await entitlements.archive_usage(uid)
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
        if await watches.count_active(uid)>=limit: await message.answer(f"Your plan allows {limit} active watch(es)."); return
        try: key,display=await InstagramWatchProvider(db.pool).resolve_target(parts[1])
        except Exception: await message.answer("I couldn't resolve that public Instagram profile right now."); return
        await watches.create(uid,"instagram",key,display,mode)
        await message.answer(f"👀 Watching @{display} for {mode}. The first poll establishes a baseline.")

    @dp.message(Command("watch"))
    async def watch(message:Message): await add_watch(message,"posts")
    @dp.message(Command("watchstories"))
    async def watchstories(message:Message): await add_watch(message,"stories")
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
        if await watches.count_active(uid)>=watch_limit:
            await message.answer(f"Your plan allows {watch_limit} active Saved Friend/watch slot(s)."); return
        archive=await entitlements.authorize_archive(uid)
        if not archive.allowed:
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
        if len(parts)!=2: await message.answer("Usage: /archive <Instagram URL>"); return
        try: canonical=normalize_instagram_url(parts[1].strip())
        except UnsupportedUrl as exc: await message.answer(f"Unsupported link: {exc}"); return
        uid=await user_id(message)
        if not await velocity_ok(message,uid): return
        decision=await entitlements.authorize_archive(uid)
        if not decision.allowed: await message.answer("Permanent archive is not included on your plan."); return
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
