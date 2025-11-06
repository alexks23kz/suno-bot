import asyncio
import io
import logging
import os
import re
import sys
from typing import Dict

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

# === Конфиг ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUNO_API_KEY = os.getenv("SUNO_API_KEY")
BASE_URL = "https://api.sunoapi.org/api/v1"
CALLBACK_PATH = "/suno-callback"
CALLBACK_URL = os.getenv("CALLBACK_URL")

if not BOT_TOKEN or not SUNO_API_KEY:
    raise ValueError("BOT_TOKEN и SUNO_API_KEY обязательны в .env!")
if not CALLBACK_URL:
    print("CALLBACK_URL обязателен! Запусти ngrok и укажи: https://твой-ngrok/suno-callback")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# task_id → (message, data, polling_task)
pending_tasks: Dict[str, tuple[types.Message, dict, asyncio.Task | None]] = {}

# === FSM ===
class GenerateStates(StatesGroup):
    choosing_mode = State()
    input_description = State()
    input_title = State()
    input_style = State()
    input_lyrics = State()
    choosing_gender = State()
    choosing_model = State()  # Новый шаг для выбора модели

# === Клавиатуры ===
def get_mode_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
        [KeyboardButton(text="По описанию (коротко)")],
        [KeyboardButton(text="По тексту песни (полный контроль)")]
    ])

def get_gender_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
        [KeyboardButton(text="Мужской голос")],
        [KeyboardButton(text="Женский голос")]
    ])

def get_model_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
        [KeyboardButton(text="V3_5")],
        [KeyboardButton(text="V4")],
        [KeyboardButton(text="V4_5")],
        [KeyboardButton(text="V4_5PLUS")],
        [KeyboardButton(text="V5")]
    ])

# === /start ===
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я — <b>Suno Music Bot</b>\n\n"
        "Создаю музыку через Suno AI.\n"
        "Выбери режим генерации:",
        reply_markup=get_mode_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await state.set_state(GenerateStates.choosing_mode)

# === FSM шаги ===
@dp.message(GenerateStates.choosing_mode, F.text.in_(["По описанию (коротко)", "По тексту песни (полный контроль)"]))
async def mode_chosen(message: types.Message, state: FSMContext):
    mode = message.text
    await state.update_data(mode=mode)
    if "По описанию" in mode:
        await message.answer("Ебани <b>описание песни</b> (до 500 символов):", parse_mode=ParseMode.HTML, reply_markup=types.ReplyKeyboardRemove())
        await state.set_state(GenerateStates.input_description)
    else:
        await message.answer("Ебани <b>название песни</b> (до 100 символов):", parse_mode=ParseMode.HTML)
        await state.set_state(GenerateStates.input_title)

@dp.message(GenerateStates.input_description)
async def get_description(message: types.Message, state: FSMContext):
    if len(message.text) > 500:
        await message.answer("Максимум 500 символов!")
        return
    await state.update_data(prompt=message.text.strip())
    await message.answer("Выбери голос:", reply_markup=get_gender_keyboard())
    await state.set_state(GenerateStates.choosing_gender)

@dp.message(GenerateStates.input_title)
async def get_title(message: types.Message, state: FSMContext):
    title = message.text.strip()
    if len(title) > 100:
        await message.answer("Максимум 100 символов!")
        return
    # Очистка спецсимволов для имени файла
    clean_title = re.sub(r'[\\/*?:"<>|]', "", title).replace(".", "_")  # Замена точки и запрещённых символов
    await state.update_data(title=clean_title)
    await message.answer("Чо, какой <b>стиль музыки</b>? (до 200 символов):", parse_mode=ParseMode.HTML)
    await state.set_state(GenerateStates.input_style)

@dp.message(GenerateStates.input_style)
async def get_style(message: types.Message, state: FSMContext):
    if len(message.text) > 500:
        await message.answer("Максимум 200 символов!")
        return
    await state.update_data(style=message.text.strip())
    await message.answer("Ебани <b>текст песни</b> (до 3000 символов):", parse_mode=ParseMode.HTML)
    await state.set_state(GenerateStates.input_lyrics)

@dp.message(GenerateStates.input_lyrics)
async def get_lyrics(message: types.Message, state: FSMContext):
    if len(message.text) > 3000:
        await message.answer("Максимум 3000 символов!")
        return
    await state.update_data(prompt=message.text.strip())
    await message.answer("Выбери голос:", reply_markup=get_gender_keyboard())
    await state.set_state(GenerateStates.choosing_gender)

@dp.message(GenerateStates.choosing_gender, F.text.in_(["Мужской голос", "Женский голос"]))
async def gender_chosen(message: types.Message, state: FSMContext):
    gender = "m" if "Мужской" in message.text else "f"  # Изменено на m/f
    await state.update_data(vocalGender=gender)
    await message.answer("Выбери модель:", reply_markup=get_model_keyboard())
    await state.set_state(GenerateStates.choosing_model)

@dp.message(GenerateStates.choosing_model, F.text.in_(["V3_5", "V4", "V4_5", "V4_5PLUS", "V5"]))
async def model_chosen(message: types.Message, state: FSMContext):
    model = message.text
    await state.update_data(model=model)
    await message.answer("Генерирую трек... Ожидаю результат от Suno API. ХЗ сколько ждать.", reply_markup=types.ReplyKeyboardRemove())
    await generate_music(message, state)

# === Генерация + polling fallback ===
async def generate_music(message: types.Message, state: FSMContext):
    data = await state.get_data()
    payload = {
        "prompt": data["prompt"],
        "customMode": "По тексту" in data["mode"],
        "instrumental": False,
        "model": data["model"],
        "styleWeight" : 0.8,
        "weirdnessConstraint": 0.65,
        "vocalGender": data["vocalGender"],
        "callBackUrl": CALLBACK_URL
    }
    if payload["customMode"]:
        payload["title"] = data.get("title", "Suno Song")
        payload["style"] = data.get("style", "unknown")

    status_msg = await message.answer("Отправляю задачу в Suno...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BASE_URL}/generate", json=payload, headers={
                "Authorization": f"Bearer {SUNO_API_KEY}",
                "Content-Type": "application/json"
            }) as resp:
                result = await resp.json()
                logger.info(f"Generate response: {result}")
                if result.get("code") != 200:
                    raise Exception(result.get("msg", "Неизвестная ошибка"))

                task_id = result["data"]["taskId"]
                pending_tasks[task_id] = (status_msg, data, None)

                await safe_edit(status_msg, f"Задача отправлена!\nID: <code>{task_id[:8]}...</code>\nЖдать теперь надо, хули...", ParseMode.HTML)

                # Запуск polling fallback
                polling_task = asyncio.create_task(polling_fallback(task_id))
                pending_tasks[task_id] = (status_msg, data, polling_task)

    except Exception as e:
        await safe_edit(status_msg, f"Ошибка: {str(e)}")
        await message.answer("Попробуй снова", reply_markup=get_mode_keyboard())
        await state.set_state(GenerateStates.choosing_mode)

# === Polling fallback ===
async def polling_fallback(task_id: str):
    await asyncio.sleep(180)  # Ждём 3 минуты
    if task_id not in pending_tasks:
        return
    message, data, _ = pending_tasks[task_id]
    await safe_edit(message, "Callback не пришёл. Проверяю статус...")
    await check_task_status(task_id, message, data)

# === Проверка статуса ===
async def check_task_status(task_id: str, message: types.Message, data: dict):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{BASE_URL}/generate/record-info?taskId={task_id}",
                headers={"Authorization": f"Bearer {SUNO_API_KEY}"}
            ) as resp:
                result = await resp.json()
                logger.info(f"Polling result: {result}")

                if result.get("code") != 200:
                    await safe_edit(message, f"Ошибка API: {result.get('msg')}")
                    return

                status = result["data"].get("status")
                if status != "SUCCESS":
                    await safe_edit(message, f"Статус: {status}...")
                    return

                tracks = result["data"]["response"]["sunoData"]
                if not tracks:
                    await safe_edit(message, "Треки не найдены")
                    return

                track = tracks[0]
                audio_url = track["audioUrl"]
                title = track.get("title", "Suno Track")
                duration = int(float(track.get("duration", 0)))

                async with session.get(audio_url) as audio_resp:
                    if audio_resp.status != 200:
                        await safe_edit(message, f"Ошибка скачивания: {audio_resp.status}")
                        return
                    audio_bytes = await audio_resp.read()

                try:
                    await bot.delete_message(message.chat.id, message.message_id)
                except:
                    pass

                await send_track(message.chat.id, audio_bytes, title, duration, data)
                if task_id in pending_tasks:
                    del pending_tasks[task_id]

        except Exception as e:
            logger.error(f"Polling error: {e}")
            await safe_edit(message, f"Ошибка: {e}")

# === ОТПРАВКА ТРЕКА ===
async def send_track(chat_id: int, audio_bytes: bytes, title: str, duration: int, data: dict):
    bio = io.BytesIO(audio_bytes)
    bio.name = f"{title}.mp3"
    audio_file = BufferedInputFile(bio.read(), filename=bio.name)

    caption = (
        f"**{title}**\n\n"
        f"Голос: *{'Мужской' if data['vocalGender'] == 'm' else 'Женский'}*\n"
        f"Вот столько секунд: `{duration}с`\n"
        f"Модель: *{data['model']}*"
    )

    try:
        await bot.send_audio(
            chat_id=chat_id,
            audio=audio_file,
            title=title,
            performer="Suno AI",
            duration=duration,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.warning(f"Markdown failed: {e}")
        try:
            await bot.send_audio(
                chat_id=chat_id,
                audio=audio_file,
                title=title,
                performer="Suno AI",
                duration=duration,
                caption=f"{title}\nГолос: {'Мужской' if data['vocalGender']=='m' else 'Женский'}\nДлительность: {duration}с\nМодель: {data['model']}"
            )
        except Exception as e2:
            logger.error(f"Send failed: {e2}")
            await bot.send_document(chat_id=chat_id, document=audio_file, caption="Ошибка отправки аудио")

    await bot.send_message(chat_id, "Готово! Хочешь ещё одну?", reply_markup=get_mode_keyboard())

# === Безопасное редактирование ===
async def safe_edit(message: types.Message, text: str, parse_mode=None):
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            parse_mode=parse_mode
        )
    except Exception as e:
        if "not found" not in str(e).lower() and "not modified" not in str(e).lower():
            logger.warning(f"Edit failed: {e}")

# === CALLBACK от Suno ===
async def suno_callback(request: web.Request):
    try:
        data = await request.json()
        logger.info(f"Callback received: {data}")
        task_id = data["data"]["task_id"]
        if task_id not in pending_tasks:
            return web.json_response({"status": "unknown"}, status=200)

        message, user_data, polling_task = pending_tasks.pop(task_id)
        if polling_task:
            polling_task.cancel()

        track = data["data"]["data"][0]
        audio_url = track["audio_url"]
        title = track.get("title", "Suno Track")
        duration = int(float(track.get("duration", 0)))

        async with aiohttp.ClientSession() as session:
            async with session.get(audio_url) as resp:
                if resp.status != 200:
                    await safe_edit(message, "Ошибка скачивания аудио")
                    return web.json_response({"status": "error"}, status=500)
                audio_bytes = await resp.read()

        try:
            await bot.delete_message(message.chat.id, message.message_id)
        except:
            pass

        await send_track(message.chat.id, audio_bytes, title, duration, user_data)

    except Exception as e:
        logger.error(f"Callback error: {e}")
    return web.json_response({"status": "received"}, status=200)

# === Запуск ===
async def main():
    app = web.Application()
    app.router.add_post(CALLBACK_PATH, suno_callback)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print(f"Callback URL: {CALLBACK_URL}")

    print("Бот запущен (polling + callback)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
