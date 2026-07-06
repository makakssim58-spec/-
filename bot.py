import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from bs4 import BeautifulSoup

# --- КОНФИГУРАЦИЯ (ВАШИ ДАННЫЕ) ---
BOT_TOKEN = "8936461950:AAH3O4sMPXf46-b96Mwpf3-EvH0EwWkuXeo"
CHAT_ID = "5140709876"
SEARCH_URL = "https://www.avito.ru/sankt-peterburg/mototsikly_i_mototehnika/mototsikly/used-ASgBAgICAkQ80k2Guw2qijQ?context=H4sIAAAAAAAA_wEmANn_YToxOntzOjE6InkiO3M6MTY6ImtLWW5KNm82RXFKZDM5VmsiO30Vz4myJgAAAA&f=ASgBAQECAkQ80k2Guw2qijQBQISOD6TOm_EC0JvxAsqb8QLCm_ECyJvxAryb8QLGm_ECxJvxAr6b8QLAm_ECAUXGmgwWeyJmcm9tIjowLCJ0byI6MTAwMDAwfQ&localPriority=0&q=%D0%BC%D0%BE%D1%82%D0%BE%D1%86%D0%B8%D0%BA%D0%BB%D1%8B&radius=0&searchRadius=0"
CHECK_INTERVAL = 300  # Проверка каждые 5 минут (300 секунд)

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)

# --- Инициализация бота и диспетчера ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Работа с базой данных ---
def init_db():
    """Создает таблицу для хранения ID отправленных объявлений."""
    conn = sqlite3.connect("avito_bot.db")
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS sent_ads
                  (ad_id TEXT PRIMARY KEY)"""
    )
    conn.commit()
    conn.close()

def is_ad_sent(ad_id: str) -> bool:
    """Проверяет, было ли объявление уже отправлено."""
    conn = sqlite3.connect("avito_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM sent_ads WHERE ad_id = ?", (ad_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_ad_as_sent(ad_id: str):
    """Сохраняет ID объявления, чтобы не отправлять его повторно."""
    conn = sqlite3.connect("avito_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sent_ads (ad_id) VALUES (?)", (ad_id,))
    conn.commit()
    conn.close()

# --- Структура данных объявления ---
@dataclass
class Ad:
    id: str
    title: str
    price: str
    url: str
    image_url: Optional[str] = None

# --- Парсинг Avito ---
def fetch_ads(search_url: str) -> List[Ad]:
    """
    Загружает страницу поиска и извлекает список объявлений.
    Возвращает список объектов Ad.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Ошибка при запросе к Avito: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    ad_items = soup.find_all("div", class_="iva-item-content")  # Основной контейнер
    ads = []

    for item in ad_items:
        try:
            # Извлечение ID
            link_tag = item.find("a", class_="iva-item-title-link")
            if not link_tag:
                continue
            ad_url = "https://www.avito.ru" + link_tag.get("href")
            ad_id = ad_url.split("_")[-1].split("?")[0]

            # Извлечение заголовка
            title = link_tag.get_text(strip=True)

            # Извлечение цены
            price_tag = item.find("span", class_="price-text")
            price = price_tag.get_text(strip=True) if price_tag else "Цена не указана"

            # Извлечение изображения
            image_tag = item.find("img", class_="image-frame-image")
            image_url = image_tag.get("src") if image_tag else None
            if image_url and not image_url.startswith("http"):
                image_url = "https:" + image_url

            ads.append(
                Ad(
                    id=ad_id,
                    title=title,
                    price=price,
                    url=ad_url,
                    image_url=image_url,
                )
            )
        except Exception as e:
            logging.error(f"Ошибка при парсинге объявления: {e}")
            continue

    return ads

# --- Отправка уведомления в Telegram ---
async def send_ad_notification(ad: Ad):
    """Формирует и отправляет сообщение о новом объявлении."""
    caption = f"<b>{ad.title}</b>\n"
    caption += f"💰 {ad.price}\n"
    caption += f"🔗 <a href='{ad.url}'>Ссылка на объявление</a>"

    try:
        if ad.image_url:
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=ad.image_url,
                caption=caption,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=caption,
                parse_mode="HTML",
            )
        logging.info(f"Отправлено уведомление: {ad.title} ({ad.id})")
    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения: {e}")

# --- Основной цикл проверки ---
async def check_new_ads():
    """Фоновый процесс, проверяющий наличие новых объявлений."""
    while True:
        logging.info("Проверка новых объявлений...")
        ads = fetch_ads(SEARCH_URL)

        # Проверяем каждое объявление на наличие в БД
        for ad in ads:
            if not is_ad_sent(ad.id):
                await send_ad_notification(ad)
                mark_ad_as_sent(ad.id)

        await asyncio.sleep(CHECK_INTERVAL)

# --- Обработчики команд бота ---
@dp.message(Command("start"))
async def start_command(message: types.Message):
    """Ответ на команду /start."""
    await message.answer(
        "🚀 Бот запущен и отслеживает новые объявления на Avito.\n"
        "Ожидайте уведомления!"
    )

@dp.message(Command("status"))
async def status_command(message: types.Message):
    """Команда /status показывает текущий URL для отслеживания."""
    await message.answer(f"📡 Отслеживается URL:\n{SEARCH_URL}")

# --- Запуск бота ---
async def main():
    init_db()
    logging.info("Бот запущен...")
    # Запускаем фоновую задачу
    asyncio.create_task(check_new_ads())
    # Запускаем обработку команд
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())