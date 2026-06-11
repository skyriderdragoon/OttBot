#!/usr/bin/env python3
import datetime
import logging

import arrow # arrow is used by ICS instead of datetime
import ics

import asyncio # used by telegram
import httpx # used by telegram
import os

# check out the telegram docs:
# https://docs.python-telegram-bot.org/en/stable/telegram.bot.html
from telegram.constants import ChatMemberStatus, ParseMode
from telegram import Update, Bot
from telegram.ext import Application, ApplicationBuilder, ContextTypes, CommandHandler, ChatJoinRequestHandler, ChatMemberHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = os.getenv("ADMIN_GROUP_ID")
MAIN_GROUP_ID = os.getenv("MAIN_GROUP_ID")
WAITING_ROOM_GROUP_ID = os.getenv("WAITING_ROOM_GROUP_ID")
NOTIFICATIONS_CHANNEL_ID = os.getenv("NOTIFICATION_CHANNEL_ID")
ICAL_URL = os.getenv("ICAL_URL")
LOCAL_TZ = 'Europe/London'

# Utilities ####################################################################

async def get_admin_set(bot:Bot) -> set[int]:
    chat_members = await bot.get_chat_administrators(MAIN_GROUP_ID)
    return {chat_member.user.id for chat_member in chat_members}

async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str, **kwargs) -> None:
    await context.bot.send_message(chat_id=update.effective_chat.id, text=message, **kwargs)

async def respond_success(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    await respond(update, context, f"✅ {message}")

async def respond_error(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    await respond(update, context, f"❌ {message}")

async def alert(bot:Bot, text: str) -> None:
    await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text)

async def announce(bot:Bot, lines: list[str], chat_id=MAIN_GROUP_ID, **kwargs) -> None:
    await bot.send_message(chat_id=chat_id,
                           parse_mode=ParseMode.HTML,
                           text="\n".join(lines),
                           **kwargs)

def sanitize(text: str|None) -> str:
    # See: https://core.telegram.org/bots/api#html-style
    if text is None:
        return ""
    else:
        # NOTE: Order matters! "&" must be escaped first!
        return text.replace("&", "&amp;")\
                   .replace("<", "&lt;")\
                   .replace(">", "&gt;")

def ordinal(n:int) -> str:
    return f"{n}th" if n//10==1 else \
           f"{n}st" if n %10==1 else \
           f"{n}nd" if n %10==2 else \
           f"{n}rd" if n %10==3 else \
           f"{n}th"

async def get_upcoming_meet_events(ical_url:str=ICAL_URL, local_tz:str=LOCAL_TZ, now=arrow.utcnow()) -> list[ics.Event]:
    """returns sorted list of events that have not yet ended"""
    async with httpx.AsyncClient() as client:
        response = await client.get(ical_url)
        text = response.raise_for_status().text
    events = list(ics.Calendar(text).events)
    events.sort(key=lambda e:e.begin)
    ret = []
    for event in filter(lambda e:now < e.end, events):
        event.begin = event.begin.to(local_tz)
        event.end   = event.end.to(local_tz)
        ret.append(event)
    return ret

# Events #######################################################################

async def waiting_room_welcome(bot, user) -> None:
    await alert(bot, f"🆕 {user.first_name} {user.last_name} (@{user.username} id:{user.id})")
    await announce(bot, [
        f"Hi {sanitize(user.first_name)}! An admin will be with you shortly to get you in the main chat.",
         "",
        "In the mean time, please let us know a bit about yourself, read <a href='https://rules.cambfurs.co.uk'>the rules</a> and let us know whether you agree."
    ], chat_id=WAITING_ROOM_GROUP_ID)

async def main_group_welcome(bot, user) -> None:
    await announce(bot, [
        f"Everyone welcome <a href='tg://user?id={user.id}'>{sanitize(user.first_name)}</a> to the chat!",
    ])

async def meet_started(bot, event) -> None:
    month_name = arrow.locales.EnglishLocale.month_names[event.begin.month]
    await announce(bot, [ f"The {month_name} meet has started!" ])

async def meet_tomorrow(bot, event) -> None:
    month_name = arrow.locales.EnglishLocale.month_names[event.begin.month]
    await announce(bot, [ f"Reminder! The {month_name} meet is tomorrow!" ])

async def meet_next_week(bot, event) -> None:
    month_name = arrow.locales.EnglishLocale.month_names[event.begin.month]
    await announce(bot, [ f"Reminder! the {month_name} meet is next week!" ])

# This callback gets called every hour at the top of the hour
async def hourly_callback(bot, now, next_events):
    for event in next_events:
        if now.floor('hour')==event.begin.floor('hour'):
            await meet_started(bot, event)
        elif now.hour == 10 and now.shift(days=1).date() == event.begin.date():
            await meet_tomorrow(bot, event)
        elif now.hour == 10 and now.shift(days=7).date() == event.begin.date():
            await meet_next_week(bot, event)

async def hourly_callback_generator(bot: Bot):
    while True:
        now = arrow.utcnow()
        await asyncio.sleep( (now.ceil('hours')-now).total_seconds() )
        now = arrow.utcnow()
        next_events = await get_upcoming_meet_events(now=now)
        await hourly_callback(bot, now, next_events)

# Telegram is a little bit silly: we can't query the chat to see who is in it.
# The solution is to listen for chat join (and leave) requests, and keep track
# of this ourselves. This misses anyone that was in the waiting room before
# catbot was live.
SEEN_MEMBERS_IN_WAITING_ROOM = set()
async def chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.chat_member.chat.id != WAITING_ROOM_GROUP_ID:
        return
    old = update.chat_member.old_chat_member
    new = update.chat_member.new_chat_member
    if old.status==ChatMemberStatus.LEFT and new.status==ChatMemberStatus.MEMBER:
        SEEN_MEMBERS_IN_WAITING_ROOM.add(new.user.id)
        await waiting_room_welcome(context.bot, new.user)
    elif old.status==ChatMemberStatus.MEMBER and new.status==ChatMemberStatus.LEFT:
        SEEN_MEMBERS_IN_WAITING_ROOM.discard(old.user.id)
    print(SEEN_MEMBERS_IN_WAITING_ROOM)

# Commands #####################################################################

COMMANDS = []

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start: initiate a CatBot conversation"""
    if update.message.chat.type!="private":
        return
    admin_set = await get_admin_set(context.bot)
    if update.message.from_user.id not in admin_set:
        await respond(update, context, "Meow!")
        return
    command_docs = '\n'.join([cmd.__doc__ for cmd in COMMANDS])
    await respond(update, context, f"Hewwo! I'm Catbot! These are the things I can do:\n{command_docs}", protect_content=True)
COMMANDS.append(cmd_start)

async def cmd_notify(update:Update, context:ContextTypes.DEFAULT_TYPE) -> None:
    """/notify: send a message to the CambFurs Notifications channel"""
    admin_set = await get_admin_set(context.bot)
    if not update.message.chat.type=="private" or update.message.chat.id==ADMIN_GROUP_ID:
        return
    if update.message.from_user.id not in admin_set:
        return
    if update.message.reply_to_message is None:
        await respond_error(update,context,"Please respond to the message you wish to send")
        return

    text = update.message.reply_to_message.text
    try:
        message = await context.bot.send_message(chat_id=NOTIFICATIONS_CHANNEL_ID, text=sanitize(text))
    except Exception as e:
        await respond_error(update,context,f"Error in sending notification due to: {e}")
        return
    try:
        forward = await context.bot.forwardMessage(chat_id=MAIN_GROUP_ID, from_chat_id=NOTIFICATIONS_CHANNEL_ID, message_id=message.id)
        await respond_success(update, context, f"Forwarded! id:{forward.id}")
    except Exception as e:
        await respond_error(update, context, f"Error forwarding message due to: {e}")
        return
COMMANDS.append(cmd_notify)


async def cmd_meet_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/meet_dates: list upcoming meet dates"""
    if not(update.message.chat.type=="private" or \
           update.message.chat.id==MAIN_GROUP_ID or \
           update.message.chat.id==ADMIN_GROUP_ID):
        return
    upcoming_events = await get_upcoming_meet_events()
    ret = ["⭐ <b><u>Upcoming meet dates</u></b> ⭐"]
    for event in upcoming_events:
        month = arrow.locales.EnglishLocale.month_names[event.begin.month]
        day = ordinal(event.begin.day)
        maybe_description = ' '+event.description if event.description is not None else ''
        ret.append(f"➡️ {month} {day}{sanitize(maybe_description)}")
    await respond(update,context, "\n".join(ret), parse_mode=ParseMode.HTML)
COMMANDS.append(cmd_meet_dates)


async def cmd_say(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/say: puts replied message into the main chat"""

    if not update.message.chat.type=="private" or update.message.chat.id==ADMIN_GROUP_ID:
        return

    admin_set = await get_admin_set(context.bot)
    if update.message.from_user.id not in admin_set:
        return

    if update.message.reply_to_message is None:
        await respond_error(update,context,"Please respond to the message you wish to send")
        return

    text = update.message.reply_to_message.text
    message = await context.bot.send_message(chat_id=MAIN_GROUP_ID, text=text)
    await respond_success(update,context,f"Sent! id: {message.id}")
COMMANDS.append(cmd_say)

# Authentication ###############################################################
# NOTE: Matching on usernames is not bullet-proof in Telegram. One user can own
# multiple 'vanity' @usernames they can switch between or even have no username
# at all. I think it's fine to rely on usernames for the short duration between
# /approving and them joining.

APPROVED_USERS_IN_WAITING_ROOM = set()

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approve @username: create invite link for user"""

    if not update.message.chat.id==WAITING_ROOM_GROUP_ID:
        return

    if not update.message.from_user.username=="GroupAnonymousBot":
        return

    user = list(update.message.parse_entities(types=['mention']).values())
    if len(user)!=1:
        await respond_error(update,context,"Must specify a single user to approve")
        return
    user = user[0]

    minutes_valid = 5
    invite_link = await context.bot.create_chat_invite_link(
        MAIN_GROUP_ID,
        creates_join_request=True,
        expire_date=datetime.datetime.now(datetime.UTC)+datetime.timedelta(minutes=minutes_valid))

    global APPROVED_USERS_IN_WAITING_ROOM
    APPROVED_USERS_IN_WAITING_ROOM.add(user)

    await respond(update, context, f"{user} Here's your invite link to the CambFurs group! This link is only valid for {minutes_valid} minutes\n\n{invite_link.invite_link}")
COMMANDS.append(cmd_approve)


async def join_request(update:Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.chat_join_request.from_user.id
    chat_id = update.chat_join_request.chat.id
    username = f"@{update.chat_join_request.from_user.username}"

    if chat_id!=MAIN_GROUP_ID:
        await alert(context.bot, f"⛔ Declined join request from {username}: requested to join chat other than main group")
        await context.bot.decline_chat_join_request(chat_id, user_id)
        return

    global APPROVED_USERS_IN_WAITING_ROOM
    if username not in APPROVED_USERS_IN_WAITING_ROOM:
        await alert(context.bot, f"⛔ Declined join request from {username}: they were not approved")
        await context.bot.decline_chat_join_request(chat_id, user_id)
        return

    APPROVED_USERS_IN_WAITING_ROOM.discard(username)
    await context.bot.approve_chat_join_request(MAIN_GROUP_ID, user_id)
    await context.bot.revoke_chat_invite_link(MAIN_GROUP_ID, update.chat_join_request.invite_link)
    await main_group_welcome(context.bot, update.chat_join_request.from_user)
    # To kick a user from the waiting room we "unban" them. This will kick a
    # member if they're in the chat by default. Yes that's a weird API decision.
    # see: https://core.telegram.org/bots/api#unbanchatmember
    await context.bot.unban_chat_member(WAITING_ROOM_GROUP_ID, user_id)

async def initialize(app: Application) -> None:
    # Exceptions escaping from the initialize function result in a silent crash.
    # It's therefore important to wrap everything in a try block
    try:
        app.create_task(hourly_callback_generator(app.bot))
    except:
        await alert(app.bot, "🆘 CatBot failed to start")
        raise
    else:
        await alert(app.bot, "🟢 CatBot started")

async def finalize(app: Application) -> None:
    await alert(app.bot, "🆘 CatBot stopped")

app = Application.builder().token(BOT_TOKEN).post_init(initialize).post_stop(finalize).build()

if __name__ == "__main__":
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("say",   cmd_say))
    app.add_handler(CommandHandler("notify", cmd_notify))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("meet_dates", cmd_meet_dates))
    app.add_handler(ChatMemberHandler(chat_member_updated, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatJoinRequestHandler(join_request))
    app.run_polling(allowed_updates=Update.ALL_TYPES)  # Update.ALL_TYPES required to get ChatMemberHandler events
