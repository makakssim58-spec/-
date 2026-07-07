import asyncio
import logging
import sqlite3
import os
from dataclasses import dataclass
from typing import List, Optional
from aiohttp import web

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from bs4 import BeautifulSoup

# ==================================================
# 1. НАСТРОЙКИ (ВАШИ ДАННЫЕ)
# ==================================================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ ОШИБКА: TELEGRAM_TOKEN не найден в переменных окружения!")

# Ваш CHAT_ID
CHAT_ID = "5140709876"

# НОВАЯ ССЫЛКА С ФИЛЬТРОМ "СНАЧАЛА НОВЫЕ"
SEARCH_URL = "https://www.avito.ru/sankt-peterburg/mototsikly_i_mototehnika/mototsikly/used-ASgBAgICAkQ80k2Guw2qijQ?context=H4sIAAAAAAAA_wEmANn_YToxOntzOjE6InkiO3M6MTY6IjFyUGJkdzlTc0dRVTBjQ2MiO301lrNPJgAAAA&f=ASgBAQECAkQ80k2Guw2qijQBQISOD6TOm_EC0JvxAsqb8QLCm_ECyJvxAryb8QLGm_ECxJvxAr6b8QLAm_ECAUXGmgwWeyJmcm9tIjowLCJ0byI6MTAwMDAwfQ&q=%D0%BC%D0%BE%D1%82%D0%BE%D1%86%D0%B8%D0%BA%D0%BB%D1%8B&radius=100&s=104&searchRadius=100"

# Интервал проверки (5 минут - теперь безопасно!)
CHECK_INTERVAL = 300

# ==================================================
# 2. НАСТРОЙКА БОТА
# ==================================================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================================================
# 3. КНОПКИ
# ==================================================
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Старт"), KeyboardButton(text="📊 Статус")],
            [KeyboardButton(text="ℹ️ Помощь"), KeyboardButton(text="🔄 Обновить")],
            [KeyboardButton(text="❓ Как это работает")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    return keyboard

def get_inline_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Проверить сейчас", callback_data="check_now"),
                InlineKeyboardButton(text="📋 Мой URL", callback_data="show_url")
            ]
        ]
    )
    return keyboard

# ==================================================
# 4. БАЗА ДАННЫХ (ТОЛЬКО ДЛЯ ID ОБЪЯВЛЕНИЙ)
# ==================================================
def init_db():
    conn = sqlite3.connect("avito_bot.db")
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS sent_ads (ad_id TEXT PRIMARY KEY)""")
    conn.commit()
    conn.close()

def is_ad_sent(ad_id: str) -> bool:
    conn = sqlite3.connect("avito_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM sent_ads WHERE ad_id = ?", (ad_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_ad_as_sent(ad_id: str):
    conn = sqlite3.connect("avito_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sent_ads (ad_id) VALUES (?)", (ad_id,))
    conn.commit()
    conn.close()

# ==================================================
# 5. ПАРСИНГ (ТОЛЬКО ПЕРВАЯ СТРАНИЦА!)
# ==================================================
@dataclass
class Ad:
    id: str
    title: str
    price: str
    url: str
    image_url: Optional[str] = None

def fetch_ads(search_url: str) -> List[Ad]:
    """Парсит ТОЛЬКО ПЕРВУЮ страницу (новые объявления всегда здесь)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Ошибка при запросе: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    ad_items = soup.find_all("div", class_="iva-item-content")
    ads = []

    for item in ad_items:
        try:
            link_tag = item.find("a", class_="iva-item-title-link")
            if not link_tag:
                continue
            ad_url = "https://www.avito.ru" + link_tag.get("href")
            ad_id = ad_url.split("_")[-1].split("?")[0]
            title = link_tag.get_text(strip=True)
            price_tag = item.find("span", class_="price-text")
            price = price_tag.get_text(strip=True) if price_tag else "Цена не указана"
            image_tag = item.find("img", class_="image-frame-image")
            image_url = image_tag.get("src") if image_tag else None
            if image_url and not image_url.startswith("http"):
                image_url = "https:" + image_url
            ads.append(Ad(id=ad_id, title=title, price=price, url=ad_url, image_url=image_url))
        except Exception as e:
            logging.error(f"Ошибка при парсинге: {e}")
            continue

    logging.info(f"Найдено {len(ads)} объявлений на первой странице")
    return ads

# ==================================================
# 6. ОТПРАВКА УВЕДОМЛЕНИЙ
# ==================================================
async def send_ad_notification(ad: Ad):
    caption = f"<b>{ad.title}</b>\n💰 {ad.price}\n🔗 <a href='{ad.url}'>Ссылка на объявление</a>"
    try:
        if ad.image_url:
            await bot.send_photo(chat_id=CHAT_ID, photo=ad.image_url, caption=caption, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=CHAT_ID, text=caption, parse_mode="HTML")
        logging.info(f"Отправлено: {ad.title} ({ad.id})")
    except Exception as e:
        logging.error(f"Ошибка при отправке: {e}")

async def check_new_ads():
    while True:
        logging.info("Проверка новых объявлений...")
        ads = fetch_ads(SEARCH_URL)
        for ad in ads:
            if not is_ad_sent(ad.id):
                await send_ad_notification(ad)
                mark_ad_as_sent(ad.id)
        await asyncio.sleep(CHECK_INTERVAL)

# ==================================================
# 7. ОБРАБОТЧИКИ КОМАНД И КНОПОК
# ==================================================
@dp.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = get_main_keyboard()
    inline_keyboard = get_inline_keyboard()
    await message.answer(
        "🏍️ <b>Мото-мониторинг Avito</b>\n\n"
        "Я отслеживаю НОВЫЕ объявления о мотоциклах в Санкт-Петербурге.\n"
        "Как только появится свежее объявление — я сразу пришлю его вам!\n\n"
        "⬇️ Используйте кнопки ниже для управления:",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await message.answer(
        "🔽 Или нажмите на кнопки под этим сообщением:",
        reply_markup=inline_keyboard
    )

@dp.message(lambda message: message.text == "🚀 Старт")
async def start_button_handler(message: types.Message):
    keyboard = get_main_keyboard()
    await message.answer(
        "✅ Бот активен! Я проверяю новые объявления каждые 5 минут.\n"
        "Как только появится новое — вы узнаете первым!",
        reply_markup=keyboard
    )

@dp.message(lambda message: message.text == "📊 Статус")
async def status_button_handler(message: types.Message):
    keyboard = get_main_keyboard()
    ads = fetch_ads(SEARCH_URL)
    total_ads = len(ads)
    await message.answer(
        f"📡 <b>Текущий URL поиска:</b>\n<code>{SEARCH_URL}</code>\n\n"
        f"📊 <b>Всего объявлений на первой странице:</b> {total_ads}\n"
        f"⏱ <b>Интервал проверки:</b> {CHECK_INTERVAL // 60} минут\n"
        f"🔄 <b>Статус:</b> Активен, ожидаю новые объявления",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.message(lambda message: message.text == "ℹ️ Помощь")
async def help_button_handler(message: types.Message):
    keyboard = get_main_keyboard()
    await message.answer(
        "🆘 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Бот автоматически проверяет новые объявления каждые 5 минут\n"
        "2️⃣ При появлении нового мотоцикла вы получите уведомление\n"
        "3️⃣ Используйте кнопки внизу для быстрого управления\n"
        "4️⃣ Если хотите проверить вручную — нажмите «🔄 Обновить»\n\n"
        "📌 <b>Важно:</b> Бот показывает ТОЛЬКО новые объявления (с фильтром «Сначала новые»)",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.message(lambda message: message.text == "🔄 Обновить")
async def refresh_button_handler(message: types.Message):
    keyboard = get_main_keyboard()
    await message.answer(
        "🔄 Начинаю проверку новых объявлений...",
        reply_markup=keyboard
    )
    
    ads = fetch_ads(SEARCH_URL)
    new_ads = []
    for ad in ads:
        if not is_ad_sent(ad.id):
            new_ads.append(ad)
            mark_ad_as_sent(ad.id)
    
    if new_ads:
        await message.answer(f"✅ Найдено {len(new_ads)} новых объявлений! Сейчас отправлю.")
        for ad in new_ads:
            await send_ad_notification(ad)
    else:
        await message.answer(
            "😴 Новых объявлений пока нет.\n"
            "Попробуйте позже!",
            reply_markup=keyboard
        )

@dp.message(lambda message: message.text == "❓ Как это работает")
async def how_it_works_handler(message: types.Message):
    keyboard = get_main_keyboard()
    await message.answer(
        "⚙️ <b>Как работает бот:</b>\n\n"
        "1. Я каждые 5 минут загружаю ПЕРВУЮ страницу Avito с вашим фильтром\n"
        "2. Фильтр «Сначала новые» гарантирует, что все свежие объявления будут наверху\n"
        "3. Сравниваю новые объявления с теми, что уже отправлены\n"
        "4. Если нахожу новое — отправляю вам уведомление\n\n"
        "✅ <b>Преимущества:</b>\n"
        "• Не нужно листать страницы\n"
        "• Меньше запросов к Avito\n"
        "• Никаких блокировок\n"
        "• Мгновенное обнаружение новых объявлений",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.callback_query()
async def handle_inline_buttons(callback: types.CallbackQuery):
    keyboard = get_main_keyboard()
    
    if callback.data == "check_now":
        await callback.message.answer("🔍 Проверяю новые объявления...")
        ads = fetch_ads(SEARCH_URL)
        new_ads = []
        for ad in ads:
            if not is_ad_sent(ad.id):
                new_ads.append(ad)
                mark_ad_as_sent(ad.id)
        
        if new_ads:
            await callback.message.answer(f"✅ Найдено {len(new_ads)} новых объявлений! Отправляю...")
            for ad in new_ads:
                await send_ad_notification(ad)
        else:
            await callback.message.answer(
                "😴 Новых объявлений нет.\n"
                "Попробуйте позже!",
                reply_markup=keyboard
            )
        await callback.answer()
    
    elif callback.data == "show_url":
        await callback.message.answer(
            f"📡 <b>Ваш URL поиска:</b>\n<code>{SEARCH_URL}</code>\n\n"
            f"🔗 <a href='{SEARCH_URL}'>Открыть в браузере</a>",
            parse_mode="HTML"
        )
        await callback.answer()

# ==================================================
# 8. ЗАГЛУШКА ДЛЯ RENDER
# ==================================================
async def health_check(request):
    return web.Response(text="OK")

# ==================================================
# 9. ЗАПУСК
# ==================================================
async def main():
    init_db()
    logging.info("✅ Бот запущен...")
    
    asyncio.create_task(check_new_ads())
    
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    logging.info("🌐 Веб-заглушка запущена на порту 10000")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
