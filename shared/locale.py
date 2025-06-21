# TELEGRAM
# meta
DESC = "Приватный Telegram-бот для информирования и быстрых ответов в каналах, а также получения кода 2FA."
DESC_SHORT = "Приватный Telegram-бот для информирования"

# commands
CMD_ID = "Ваш Telegram ID"

# placeholders
WIP = "Недоступно"

# messages
# Markdown is supported: *bold*, _italic_, ``, ``` ```, [text](https://url/)
ERR_PREFIX = "❗️ Не удалось отправить сообщение: "
ID_RESPONSE = ("Ваш Telegram ID: `{tg_id}`\n\n"
               "_Чтобы получать системные уведомления от бота, убедитесь, что администратор добавил ваш ID._")
DEFAULT_RESPONSE = ("Здравствуйте! Данный Telegram-бот предназначен для информирования "
                    "и взаимодействия в приватных каналах, и не принимает команды напрямую.")
ON_JOIN_DEFAULT = ("Бот успешно добавлен.\n"
                   "Telegram ID канала: `{channel_id}`\n\n"
                   "_Пожалуйста укажите этот ID при добавлении инстанса в админ-панели._")
ON_JOIN_INSTANCE = ("Бот успешно добавлен.\n\n"
                    "_Здесь будут публиковаться новые сообщения, уведомления и обрабатываться быстрые ответы для "
                    "инстанса {instances}._")
ON_INSTANCE_ID_CHANGED = "ID привязанного инстанса: `{old_inst}` → `{new_inst}`"
ON_INSTANCE_STATE_CHANGED = "Статус инстанса {instance}: `{old_state}` → `{new_state}`"
NEW_MESSAGE = ("*От:* {name}\n"
               "*Номер:* `{phone}`\n"
               "*Сообщение:* {text}")
NEW_CALL = ("📞 *Входящий звонок*\n"
            "*От:* {name}\n"
            "*Номер:* `{phone}`")

# unused
ON_INSTANCE_ADDED = ("Инстанс {instance} привязан к этому каналу.\n\n"
                     "_Теперь здесь будут публиковаться новые сообщения, уведомления и обрабатываться быстрые ответы._")
ON_INSTANCE_DELETED = ("Инстанс {instance} и все его данные удалены. Новые сообщения и уведомления публиковаться "
                       "не будут.")

# misc
# emojis should be in channel reactions
REACT_OK = "👍"
REACT_FAIL = "😡"

# WEBAPP
