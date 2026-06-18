#!/usr/bin/env python3
import datetime
import os
import re
import pathlib

import arrow
# import asyncio  # used by telegram
import httpx  # used by telegram
import ics
import telegram.constants
from ptbcontrib.roles import setup_roles, RolesHandler
from telegram import Update, Bot
# check out the telegram docs:
# https://docs.python-telegram-bot.org/en/stable/telegram.bot.html
from telegram.constants import (
    ChatMemberStatus,
    ParseMode,
    ChatType
)
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    Defaults,
    MessageHandler,
    filters,
    PicklePersistence,
    PersistenceInput
)
from telegram.ext.filters import UpdateFilter

# Setup and check environment variables #########################################
_configVariables = dict([
    ("BOT_TOKEN", os.getenv("BOT_TOKEN")),
    ("ADMIN_GROUP_ID", os.getenv("ADMIN_GROUP_ID")),
    ("MAIN_GROUP_ID", os.getenv("MAIN_GROUP_ID")),
    ("WAITING_ROOM_GROUP_ID", os.getenv("WAITING_ROOM_GROUP_ID")),
    ("WAITING_ROOM_TOPICS_ID", os.getenv("WAITING_ROOM_TOPICS_ID")),
    ("NOTIFICATIONS_CHANNEL_ID", os.getenv("NOTIFICATION_CHANNEL_ID")),
    ("ICAL_URL", os.getenv("ICAL_URL")),
])


def check_config():
    for index, value in _configVariables.items():
        if value is not None and len(value) > 0:
            print(f"{index} holds a value")
            pass
        else:
            raise ValueError(index, value)
    print("All config variables are not None or empty")


LOCAL_TZ = 'Europe/London'
locales = arrow.locales
defaults = Defaults(
    tzinfo=arrow.get(tzinfo="Europe/London").tzinfo,
)
p = pathlib.Path.cwd() / "data" / "ottbot"
ottbot_persistence_path = p.resolve()
# Setup for persistence
ottbot_persistence = PicklePersistence(
    filepath=ottbot_persistence_path,
    store_data=(
        PersistenceInput(
            bot_data=False,
            chat_data=True,
            user_data=True,
            callback_data=False,
        )
    ),
    update_interval=60
)


# Setup ########################################################################


def main() -> None:
    check_config()
    app = Application \
        .builder() \
        .token(_configVariables.get("BOT_TOKEN")) \
        .defaults(defaults) \
        .persistence(ottbot_persistence) \
        .post_init(initialize) \
        .post_stop(finalize) \
        .build()

    job_queue = app.job_queue
    app.run_polling(allowed_updates=Update.ALL_TYPES)  # Update.ALL_TYPES required to get ChatMemberHandler events


# Utilities ####################################################################


class ForwardOrigin:
    def __init__(self, user: telegram.User):
        self.first_name = user.first_name
        self.last_name = user.last_name
        self.id = user.id
        self.username = user.username

    @property
    def get_id(self):
        return self.id


class FilterWaitingRoom(UpdateFilter):
    def filter(self, update):
        return update.effective_chat.id == _configVariables.get("WAITING_ROOM_TOPICS_ID")
filter_waiting_room = FilterWaitingRoom()


class FilterDirect(UpdateFilter):
    def filter(self, update):
        return update.effective_chat.type == ChatType.PRIVATE
filter_direct_message = FilterDirect()


async def add_to_sfw_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_user.id)

    result = await context.bot.get_chat_member(chat_id=_configVariables.get("MAIN_GROUP_ID"), user_id=user_id)
    if result == ChatMemberStatus.MEMBER:
        context.roles["sfw_member"].add_member(user_id)


def set_topic_from_user(user) -> str:
    name = f"{user.username} ({user.id})"
    print(name)
    return name


async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str, **kwargs) -> None:
    await context.bot.send_message(chat_id=update.effective_chat.id, text=message, **kwargs)


async def respond_success(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    await respond(update, context, f"✅ {message}")
    update.effective_chat.id


async def respond_error(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    await respond(update, context, f"❌ {message}")


async def alert(bot: Bot, text: str) -> None:
    await bot.send_message(chat_id=_configVariables.get("ADMIN_GROUP_ID"), text=text)


async def announce(bot: Bot, lines: list[str], chat_id=_configVariables.get("MAIN_GROUP_ID"), **kwargs) -> None:
    await bot.send_message(chat_id=chat_id,
                           parse_mode=ParseMode.HTML,
                           text="\n".join(lines),
                           **kwargs)


def sanitize(text: str | None) -> str:
    # See: https://core.telegram.org/bots/api#html-style
    if text is None:
        return ""
    else:
        # NOTE: Order matters! "&" must be escaped first!
        return text.replace("&", "&amp;") \
            .replace("<", "&lt;") \
            .replace(">", "&gt;")


def ordinal(n: int) -> str:
    return  f"{n}th" if n // 10 == 1 else \
            f"{n}st" if n % 10 == 1 else \
            f"{n}nd" if n % 10 == 2 else \
            f"{n}rd" if n % 10 == 3 else \
            f"{n}th"


async def get_upcoming_meet_events(
        ical_url: str = _configVariables.get("ICAL_URL"),
        local_tz: str = LOCAL_TZ,
        now=arrow.utcnow()) -> list[ics.Event]:
    """returns sorted list of events that have not yet ended"""
    async with httpx.AsyncClient() as client:
        response = await client.get(ical_url)
        text = response.raise_for_status().text
    events = list(ics.Calendar(text).events)
    events.sort(key=lambda e: e.begin)
    ret = []
    for event in filter(lambda e: now < e.end, events):
        event.begin = event.begin.to(local_tz)
        event.end = event.end.to(local_tz)
        ret.append(event)
    return ret


# Events #######################################################################


async def waiting_room_welcome(bot, user) -> None:
    await alert(bot, f"🆕 {user.first_name} {user.last_name} (@{user.username} id:{user.id})")
    await announce(bot, [
        f"Hi {sanitize(user.first_name)}! An admin will be with you shortly to get you in the main chat.",
        "",
        "In the mean time, please let us know a bit about yourself, read <a href='https://rules.cambfurs.co.uk'> \
        the rules</a> and let us know whether you agree."
    ], chat_id=_configVariables.get("WAITING_ROOM_GROUP_ID"))


async def main_group_welcome(bot, user) -> None:
    await announce(bot, [
        f"Everyone welcome <a href='tg://user?id={user.id}'>{sanitize(user.first_name)}</a> to the chat!",
    ])


async def meet_started(bot, event) -> None:
    month_name = locales.EnglishLocale.month_names[event.begin.month]
    await announce(bot, [f"The {month_name} meet has started!"])


async def meet_tomorrow(bot, event) -> None:
    month_name = locales.EnglishLocale.month_names[event.begin.month]
    await announce(bot, [f"Reminder! The {month_name} meet is tomorrow!"])


async def meet_next_week(bot, event) -> None:
    month_name = locales.EnglishLocale.month_names[event.begin.month]
    await announce(bot, [f"Reminder! the {month_name} meet is next week!"])


# This callback gets called every hour at the top of the hour
async def hourly_callback(bot):
    now = arrow.utcnow()
    next_events = await get_upcoming_meet_events(now=now)
    for event in next_events:
        if now.floor('hour') == event.begin.floor('hour'):
            await meet_started(bot, event)
        elif now.hour == 10 and now.shift(days=1).date() == event.begin.date():
            await meet_tomorrow(bot, event)
        elif now.hour == 10 and now.shift(days=7).date() == event.begin.date():
            await meet_next_week(bot, event)


# Telegram is a bit silly: we can't query the chat to see who is in it.
# The solution is to listen for chat join (and leave) requests, and keep track
# of this ourselves. This misses anyone that was in the waiting room before
# catbot was live.
SEEN_MEMBERS_IN_WAITING_ROOM = set()


async def chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.chat_member.chat.id != _configVariables.get("WAITING_ROOM_GROUP_ID"):
        return
    old = update.chat_member.old_chat_member
    new = update.chat_member.new_chat_member
    if old.status == ChatMemberStatus.LEFT and new.status == ChatMemberStatus.MEMBER:
        SEEN_MEMBERS_IN_WAITING_ROOM.add(new.user.id)
        await waiting_room_welcome(context.bot, new.user)
    elif old.status == ChatMemberStatus.MEMBER and new.status == ChatMemberStatus.LEFT:
        SEEN_MEMBERS_IN_WAITING_ROOM.discard(old.user.id)
    print(SEEN_MEMBERS_IN_WAITING_ROOM)


async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, date: datetime, message_id: int,
                   channel_id: str) -> str:
    print(f"Received schedule request for deletion of {message_id} in {channel_id} at {date}")
    tz = arrow.get(tzinfo="Europe/London").tzinfo
    if arrow.utcnow().astimezone(tz) < date:
        try:
            context.job_queue.run_once(callback=delete, when=date.datetime, chat_id=channel_id, name=f"{message_id}",
                                       data=message_id)
            print("job scheduled")
            message = str(message_id) + " scheduled"
            return message
        except Exception as e:
            await respond_error(update, context, f"Failed to schedule job: {e}")
    else:
        print(f"Date received is in the past, cancelling request for job")
        await update.effective_chat.send_message("Hey\! I'm an otter not a time traveller\!")


async def delete(_context: ContextTypes.DEFAULT_TYPE) -> None:
    job = _context.job
    try:
        await _context.bot.deleteMessage(chat_id=job.chat_id, message_id=job.data)
        await _context.bot.send_message(chat_id=_configVariables.get("ADMIN_GROUP_ID"),
                                        text="Scheduled message deletion completed")
    except Exception as e:
        await _context.bot.send_message(chat_id=_configVariables.get("ADMIN_GROUP_ID"),
                                        text=f"Failed to complete scheduled deletion job for {job.data}: {e}")


# Commands #####################################################################

COMMANDS = []


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start: initiate a CatBot conversation"""
    command_docs = '\n'.join([cmd.__doc__ for cmd in COMMANDS])
    await respond(update, context, f"Hewwo! I'm 0ttB0t! These are the things I can do:\n{command_docs}",
                  protect_content=True)


COMMANDS.append(cmd_start)


async def cmd_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/notify {pin} {datetime}: send a message to the CambFurs Notifications channel
        Modifiers:
            pin: pins the message in the chat
            Markdown formatted Date: Schedules the message for deletion at the set time"""

    print(update.message.text_markdown_v2)
    if update.message.reply_to_message is None:
        await respond_error(update, context, "Please respond to the message you wish to send")
        return

    # Modifier Handlers
    protocol = update.message.text_markdown_v2.split(" ")
    pin_message = None
    delete_time = None
    for val in protocol:
        if val == "pin":
            pin_message = True
            print("Set Pin True")
        if re.search("((tg:)(\/{2})(time\?unix=)(\d{10,})\))$", val):
            value = re.findall('\d{10,}', val)
            value = int(value[0])
            # offset = value - datetime.datetime.now()
            tz = arrow.get(tzinfo="Europe/London")
            delete_time = tz.fromtimestamp(value)

    # Message Handler
    text = update.message.reply_to_message.text_html
    try:
        message = await context.bot.send_message(
            chat_id=_configVariables.get("NOTIFICATIONS_CHANNEL_ID"),
            text=text,
            parse_mode=ParseMode.HTML)
    except Exception as e:
        await respond_error(update, context, f"Error in sending notification due to: {e}")
        return
    try:
        forward = await context.bot.forwardMessage(chat_id=_configVariables.get("MAIN_GROUP_ID"),
                                                   from_chat_id=_configVariables.get("NOTIFICATIONS_CHANNEL_ID"),
                                                   message_id=message.id)
        success_message = f"Forwarded id: {forward.id}"
        if pin_message:
            try:
                await context.bot.pinChatMessage(chat_id=_configVariables.get("MAIN_GROUP_ID"),
                                                 message_id=forward.message_id)
                success_message.join("\rMessage Pinned")
            except Exception as e:
                await respond_error(update, context, f"Error pinning message due to: {e}")
                return
        if delete_time is not None:
            try:
                print(delete_time)
                notification = await schedule(update, context, delete_time, message.id,
                                              _configVariables.get("NOTIFICATIONS_CHANNEL_ID"))
                forward = await schedule(update, context, delete_time, forward.id,
                                         _configVariables.get("MAIN_GROUP_ID"))
                success_message.join(f"\rMessage Scheduled for deletion: {delete_time}")
                print(notification, forward)
            except Exception as e:
                print(e)
                await respond_error(update, context, f"Error scheduling deletion message due to: {e}")
                return
        await respond_success(update, context, success_message)
        print(success_message)
    except Exception as e:
        await respond_error(update, context, f"Error forwarding message due to: {e}")
        return


COMMANDS.append(cmd_notify)


async def cmd_cancel_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """/canceljob {message id}: Cancels the scheduled deletion of a specified message"""
    job_id = re.findall(r"\d{10,}", update.message.text)

    current_jobs = context.job_queue.get_jobs_by_name(job_id[0])
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True


COMMANDS.append(cmd_cancel_deletion)


async def cmd_meet_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/meet_dates: list upcoming meet dates"""
    upcoming_events = await get_upcoming_meet_events()
    ret = ["⭐ <b><u>Upcoming meet dates</u></b> ⭐"]
    for event in upcoming_events:
        month = arrow.locales.EnglishLocale.month_names[event.begin.month]
        day = ordinal(event.begin.day)
        maybe_description = ' ' + event.description if event.description is not None else ''
        ret.append(f"➡️ {month} {day}{sanitize(maybe_description)}")
    await respond(update, context, "\n".join(ret), parse_mode=ParseMode.HTML)


COMMANDS.append(cmd_meet_dates)


async def cmd_say(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/say: puts replied message into the main chat"""
    if update.message.reply_to_message is None:
        await respond_error(update, context, "Please respond to the message you wish to send")
        return

    text = update.message.reply_to_message.text_markdown_v2_urled
    message = await context.bot.send_message(chat_id=_configVariables.get("MAIN_GROUP_ID"), text=text)
    await respond_success(update, context, f"Sent! id: {message.id}")


COMMANDS.append(cmd_say)


async def cmd_register_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/registerasadmin: If you have admin role in the main chat, and are in the admin chat,
     this will give you enhanced permissions"""
    if update.effective_chat.type == ChatType.PRIVATE:
        uid = update.effective_user.id
        chat_members = await context.bot.get_chat_administrators(_configVariables.get("MAIN_GROUP_ID"))
        admin_members = await context.bot.get_chat_member(chat_id=_configVariables.get("MAIN_GROUP_ID"), user_id=uid)
        if uid in context.roles.admins.chat_ids:
            context.bot.send_message(chat_id=uid, text="You're already an admin!")
            return
        elif uid in chat_members & uid in admin_members:
            try:
                context.roles.add_admin(uid)
                context.bot.send_message(chat_id=uid,
                                         text="You've been added as an admin! You can now use all available commands")
                return
            except Exception as e:
                await update.message.reply_text(f"Error! Could not register you as an admin due to: {e}")
                return
        else:
            await context.bot.send_message(chat_id=uid,
                                           text="Hey! Very sneaky but I can't add you as an admin unless you are one!")
            return
    context.bot.send_message(chat_id=update.effective_chat, text="This command can only be used privately!")


COMMANDS.append(cmd_register_admin)


# Waiting Room Handlers#########################################################

async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/join:"""
    print("Join")
    user = update.effective_user
    print(user)
    member_type = await context.bot.get_chat_member(_configVariables.get("MAIN_GROUP_ID"), user.id)
    print(member_type)
    if member_type.status == ChatMemberStatus.MEMBER or member_type.status == ChatMemberStatus.ADMINISTRATOR:
        await update.message.reply_text("You're already a member!")
        return
    if member_type.status == ChatMemberStatus.BANNED:
        await update.message.reply_text("You cannot apply to rejoin Cambridge Furs as you have previously been banned.")
    if member_type.status == ChatMemberStatus.LEFT:
        print("Hit LEFT")
        name = set_topic_from_user(user)
        print(name)
        topic = await context.bot.create_forum_topic(chat_id=_configVariables.get("WAITING_ROOM_TOPICS_ID"), name=name)
        await context.bot.forwardMessage(
            chat_id=_configVariables.get("WAITING_ROOM_TOPICS_ID"),
            from_chat_id=user.id,
            message_thread_id=topic.message_thread_id,
            message_id=update.message.id
        )
        print(topic.message_thread_id)
        # Here we're using the data functions to work around a bad API design where you can't get easily access this
        # information elsewhere
        context.chat_data[topic.message_thread_id] = {"name": user.username, "id": user.id}
        context.user_data["thread_id"] = topic.message_thread_id
        await ottbot_persistence.flush()


COMMANDS.append(cmd_join)


async def message_reply_from_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ### Why the fuck isn't this working ###
    print(update.message.message_thread_id)
    data = context.chat_data.get(update.message.message_thread_id)
    print(data)
    if data is None:
        print("No valid chat_data held for this topic. Attempting another validation step")
        origin = update.message.reply_to_message.forward_origin
        if origin is None:
            print("Alternative validation step failed. Requesting new message with forwarded origin")
            await update.message.reply_text("Please reply to a forwarded message to continue this conversation.")
            return
        else:
            print("Alternative validation successful. Saving Chat_data for this topic")
            effective_user = ForwardOrigin(origin["sender_user"])
            context.chat_data[update.message.message_thread_id] = \
                {
                    "name": effective_user.username,
                    "id": effective_user.id
                }
            user_id = effective_user.id
    else:
        user_id = data["id"]
    await context.bot.send_message(chat_id=user_id, text=update.message.text_html, parse_mode=ParseMode.HTML)


async def message_reply_from_joiner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("message_reply_from_joiner triggered")
    thread_id = int
    try:
        thread_id = context.user_data.get("thread_id")
        print(thread_id)
    except KeyError:
        thread_id = await handle_lost_joiner_mapping(update, context)
    finally:
        try:
            await context.bot.forwardMessage(
                chat_id=_configVariables.get("WAITING_ROOM_TOPICS_ID"),
                from_chat_id=update.effective_user.id,
                message_thread_id=thread_id,
                message_id=update.message.id
            )
        except Exception as e:
            print(e)


async def handle_lost_joiner_mapping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    name = set_topic_from_user(user)
    print(f"Lost joiner mapping triggered for conversation with {name}")
    topic = await context.bot.create_forum_topic(chat_id=_configVariables.get("WAITING_ROOM_TOPICS_ID"), name=name)
    context.chat_data[topic.message_thread_id] = {"name": user.username, "id": user.id}
    context.user_data["thread_id"] = topic.message_thread_id
    return topic.message_thread_id


async def cmd_topic_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    #TODO: Not yet implemented, implement a keyboard/command button
    _chat_data = ottbot_persistence.get_chat_data()
    print(_chat_data)

    _user_data = ottbot_persistence.get_user_data()
    print(_user_data)
    try:
        await ottbot_persistence.update_chat_data(update.effective_chat.id, _chat_data)
        # await ottbot_persistence.update_user_data(_chat_data.)
    except Exception as e:
        print(e)


# Authentication ###############################################################
# NOTE: Matching on usernames is not bullet-proof in Telegram. One user can own
# multiple 'vanity' @usernames they can switch between or even have no username
# at all. I think it's fine to rely on usernames for the short duration between
# /approving and them joining.

APPROVED_USERS_IN_WAITING_ROOM = set()


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approve @username: create invite link for user"""

    if not update.message.chat.id == _configVariables.get("WAITING_ROOM_GROUP_ID"):
        return

    if not update.message.from_user.username == "GroupAnonymousBot":
        return

    user = list(update.message.parse_entities(types=['mention']).values())
    if len(user) != 1:
        await respond_error(update, context, "Must specify a single user to approve")
        return
    user = user[0]

    minutes_valid = 5
    invite_link = await context.bot.create_chat_invite_link(
        _configVariables.get("MAIN_GROUP_ID"),
        creates_join_request=True,
        expire_date=datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=minutes_valid))

    global APPROVED_USERS_IN_WAITING_ROOM
    APPROVED_USERS_IN_WAITING_ROOM.add(user)

    await respond(update, context,
                  f"{user} Here's your invite link to the CambFurs group!\
                   This link is only valid for {minutes_valid} minutes\n\n{invite_link.invite_link}")


COMMANDS.append(cmd_approve)


async def join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.chat_join_request.from_user.id
    chat_id = update.chat_join_request.chat.id
    username = f"@{update.chat_join_request.from_user.username}"

    if chat_id != _configVariables.get("MAIN_GROUP_ID"):
        await alert(context.bot,
                    f"⛔ Declined join request from {username}: requested to join chat other than main group")
        await context.bot.decline_chat_join_request(chat_id, user_id)
        return

    global APPROVED_USERS_IN_WAITING_ROOM
    if username not in APPROVED_USERS_IN_WAITING_ROOM:
        await alert(context.bot, f"⛔ Declined join request from {username}: they were not approved")
        await context.bot.decline_chat_join_request(chat_id, user_id)
        return

    APPROVED_USERS_IN_WAITING_ROOM.discard(username)
    await context.bot.approve_chat_join_request(_configVariables.get("MAIN_GROUP_ID"), user_id)
    await context.bot.revoke_chat_invite_link(_configVariables.get("MAIN_GROUP_ID"),
                                              update.chat_join_request.invite_link)
    await context.roles["sfw_member"].add_member(user_id)
    await main_group_welcome(context.bot, update.chat_join_request.from_user)
    # To kick a user from the waiting room we "unban" them. This will kick a
    # member if they're in the chat by default. Yes that's a weird API decision.
    # see: https://core.telegram.org/bots/api#unbanchatmember
    await context.bot.unban_chat_member(_configVariables.get("WAITING_ROOM_GROUP_ID"), user_id)


# Setup runners ###############################################################

async def role_setup(app: Application):
    roles = setup_roles(app)
    roles.add_admin(int(_configVariables.get("ADMIN_GROUP_ID")))
    chat_admins = await app.bot.get_chat_administrators(_configVariables.get("MAIN_GROUP_ID"))
    for value in chat_admins:
        roles.add_admin(value.user.id)
        print(f"Added {value.user.name} ({value.user.id}) as admin")

    if 'sfw_member' not in roles:
        roles.add_role(name="sfw_member")
    sfw_member = roles["sfw_member"]
    print("Role set up: sfw_member")

    roles.admins.add_child_role(sfw_member)
    print("Role set as parent: admin > sfw_member")
    return roles


# noinspection PyTypeChecker
async def initialize(app: Application) -> None:
    # Exceptions escaping from the initialize function result in a silent crash.
    # It's therefore important to wrap everything in a try block
    try:
        print("Started Initialisation")
        roles = await role_setup(app)
        # General Commands
        app.add_handler(ChatJoinRequestHandler(join_request), group=0)
        app.add_handler(CommandHandler("meet_dates", cmd_meet_dates))
        app.add_handler(ChatMemberHandler(chat_member_updated, ChatMemberHandler.CHAT_MEMBER))
        app.add_handler(CommandHandler("registerasadmin", cmd_register_admin))
        app.add_handler(CommandHandler("join", cmd_join))
        app.add_handler(RolesHandler(MessageHandler(
                                        filter_waiting_room,
                                        message_reply_from_admin),
                                    roles=roles.admins))
        app.add_handler(RolesHandler(MessageHandler(
                                        filter_direct_message,
                                        message_reply_from_joiner),
                                    roles=~roles["sfw_member"]))
        # Admin Commands
        app.add_handler(RolesHandler(CommandHandler("start", cmd_start), roles=roles.admins))
        app.add_handler(RolesHandler(CommandHandler("say", cmd_say), roles=roles.admins))
        app.add_handler(RolesHandler(CommandHandler("notify", cmd_notify), roles=roles.admins))
        app.add_handler(RolesHandler(CommandHandler("canceljob", cmd_cancel_deletion), roles=roles.admins))
        app.add_handler(RolesHandler(CommandHandler("approve", cmd_approve), roles=roles.admins))
        job_hourly_callback = app.job_queue.run_repeating(
            hourly_callback,
            interval=3600,
            first=arrow.utcnow().ceil("hour").datetime)
    except Exception as e:
        await alert(app.bot, f"🆘 0ttbot failed to start {e}")
        raise
    else:
        await alert(app.bot, "🟢 0ttbot started")


async def finalize(app) -> None:
    await alert(app.bot, "🆘 CatBot stopped")


main()
