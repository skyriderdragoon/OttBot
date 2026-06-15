# Cambfurs Catbot
Telegram bot for the Cambridge Furs group.

## Building
```
pip install -e .
```

### Permissions
CatBot expects to be in 5 groups:
1) a **main group** where CatBot has these **admin** permissions:
    * Add Users
    * Pin Messages
    * Send Messages
2) an **admin group** where CatBot has these **user** permissions:
    * Send Messages
3) a **waiting room group** where CatBot has these **admin** permissions:
    * Ban Users
4) a **notifications channel** where it has these permissions
    * Pin Messages
    * Send Messages
    * Delete Messages


Catbot expects [Privacy Mode](https://core.telegram.org/bots/features#privacy-mode) to be turned off.
That way it can read messages you give it in the admin group and waiting room group without being admin.

### Environment Variables
Catbot requires a the follow variables set as environment variables for your IDE's build configuration.
```python
BOT_TOKEN = "<YOUR TELEGRAM BOT TOKEN>"
MAIN_GROUP_ID = -1234567890
ADMIN_GROUP_ID = -1234567890
WAITING_ROOM_GROUP_ID = -1234567890
ICAL_URL="<CURRENT MEET DATES ICAL>"
```
For security reasons, this file must never be checked in.

## Contributing

### Automated checkers
We use `ruff` to help validate the python.
```bash
ruff check main.py
```

### Design principles
1) **KISS and Be Beginner Friendly**
    Keep It Simple, Stupid!
    CatBot aims to be easy to understand and easy to modify by non-experts.
    To that end, any catbot improvement must be weighed against the complexity it will introduce.
2) **No Caching**
    CatBot is a low-volume telegram bot, it is therefore not necessary to cache information.
    This reduces complexity and prevents stale data bugs.
3) **Fixed Configuration**
    CatBot does not require the flexibility of being added to arbitrary groups.
    It knows about the three groups it will be in, and has different behaviour for each.
    Having this configuration be fixed is both safer and less complex than having it be editable at run-time.
4) **Fail Safe**
    Any long-running system will experience downtime at some point.
    In the case catbot goes down, it should be possible for admins to manually do what catbot does automatically.
5) **[Least Privilege](https://en.wikipedia.org/wiki/Principle_of_least_privilege)**
    Catbot should have the minimal priviliges it needs. This limits the damage that any bugs can do.

### Roadmap to Minimum Viable Catbot
- [x] `/say` command for use in the admin group to have catbot put a message in the main group
- [x] Allow new users to join the main chat using the `/approve @username` command in the waiting room.
- [x] Welcome message in the main chat
- [x] Welcome message in the waiting room
- [x] Meet announcements generated from ical

#### Nice-to-haves
- [ ] Ability to edit messages sent by `/say`

#### Open questions
- [ ] How to implement tests for Catbot? Pre-recorded json messages?
- [ ] Should meet-dates be editable via Catbot?
- [ ] Should catbot be able to read an external banlist?
- [ ] Should the welcome list be editable at run-time?
- [ ] Should there be a "safe mode" to temporarily disable automatic actions for catbot in case it causes issues?

