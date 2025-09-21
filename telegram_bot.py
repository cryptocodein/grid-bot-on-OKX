from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os, signal
import asyncio
import logging


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("grid.log"),
        logging.StreamHandler()
    ]
)

class TelegramBot:

    def __init__(self, shutdown_coroutine, tg_token: str, trading: object, allowed_user: str, chat_id: int = None):
        self.bot = Bot(token=tg_token)
        self.dp = Dispatcher()
        self.shutdown_coroutine = shutdown_coroutine
        self.bot_work = True
        self.chat_id = chat_id
        self.stopping = False
        self.trading = trading
        self.allowed_user = allowed_user

        self.keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Запустить бота", callback_data="start_bot")],
            [InlineKeyboardButton(text="⏸️ Остановить бота", callback_data="stop_bot")],
            [InlineKeyboardButton(text="💤 Остановить программу", callback_data="stop_all")]
        ])

        self.back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ])

        self.dp.message.register(self.cmd_start, Command("start"))
        self.dp.callback_query.register(self.handle_bot_control)

    async def cmd_start(self, message: types.Message):
        if message.from_user.username != self.allowed_user:
            return
        self.chat_id = message.chat.id
        logging.info(f"TG_BOT: chat_id: {self.chat_id}")
        await self.send_message(message.chat.id, "🔀 Управление:", reply_markup=self.keyboard)

    async def handle_bot_control(self, callback: types.CallbackQuery):
        if callback.from_user.username != self.allowed_user:
            await callback.answer(f"Недостаточно прав, обратитесь к @{self.allowed_user}", show_alert=True)
            return

        if callback.data == "start_bot":
            self.bot_work = True
            logging.info(f"TG_BOT: start bot")
            await self.edit_message(callback.message, "▶️ Торговый бот запущен.", reply_markup=self.back_keyboard)

        elif callback.data == "stop_bot":
            self.bot_work = False
            logging.info(f"TG_BOT: stop bot")
            await self.edit_message(callback.message, "⏸️ Торговый бот остановлен. Если остались незакрытые ордера - закройте их вручную.", reply_markup=self.back_keyboard)

        elif callback.data == "stop_all":
            if self.stopping:
                await callback.answer("Остановка уже выполняется, подождите...", show_alert=True)
                return
            self.stopping = True
            logging.info(f"TG_BOT: stop all")
            await self.edit_message(callback.message, "💤 Торговый бот выключен. Если остались незакрытые ордера - закройте их вручную.", reply_markup=self.back_keyboard)
            # Cancel all active strategy orders before shutdown
            try:
                await self.shutdown_coroutine()
            except Exception as e:
                logging.exception(f"Error during shutdown: {e}")

            asyncio.create_task(self.delayed_exit())

        elif callback.data == "back_to_main":
            logging.info(f"TG_BOT: back to main menu")
            await self.edit_message(callback.message, "🔀 Управление:", reply_markup=self.keyboard)

    async def send_message(self, chat_id: int, text: str, reply_markup=None, parse_mode=None):
        """Асинхронная функция отправки сообщения в Telegram"""
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            logging.info(f"TG_BOT: Сообщение отправлено в чат {chat_id}")
        except Exception as e:
            logging.error(f"TG_BOT: Ошибка отправки сообщения: {e}")

    async def edit_message(self, message: types.Message, text: str, reply_markup=None):
        """Асинхронная функция редактирования сообщения в Telegram"""
        try:
            await message.edit_text(
                text=text,
                reply_markup=reply_markup
            )
            logging.info(f"TG_BOT: Сообщение отредактировано")
        except Exception as e:
            logging.error(f"TG_BOT: Ошибка редактирования сообщения: {e}")

    async def delayed_exit(self):
        await asyncio.sleep(1)
        os.kill(os.getpid(), signal.SIGINT)

    async def start(self):
        await self.dp.start_polling(self.bot)