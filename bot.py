import asyncio
import logging
import sqlite3
import os  # <--- Модуль os для работы с переменными окружения
import time
from dataclasses import dataclass
from typing import List, Optional
from aiohttp import web  # <--- Для веб-заглушки на Render

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from bs4 import BeautifulSoup

# ==================================================
# 1. ПОЛУЧЕНИЕ ТОКЕНА ИЗ ПЕРЕМЕННОЙ ОКРУЖЕНИЯ
# ==================================================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ЭТОТ БЛОК ПРОВЕРЯЕТ, ЕСТЬ ЛИ ТОКЕН, И ЕСЛИ НЕТ - ПИШЕТ ОШИБКУ В ЛОГИ
if not BOT_TOKEN:
    raise ValueError("❌ ОШИБКА: TELEGRAM_TOKEN не найден в переменных окружения! Добавьте его на Render.")

# Ваши остальные настройки
CHAT_ID = "5140709876"
SEARCH_URL = "https://www.avito.ru/sankt-peterburg/mototsikly_i_mototehnika/mototsikly/used-ASgBAgICAkQ80k2Guw2qijQ?context=H4sIAAAAAAAA_wEmANn_YToxOntzOjE6InkiO3M6MTY6Ikd3ZktHajdJWnB4NU15bloiO33-VSdcJgAAAA&f=ASgBAQECAkQ80k2Guw2qijQBQISOD6TOm_EC0JvxAsqb8QLCm_ECyJvxAryb8QLGm_ECxJvxAr6b8QLAm_ECAUXGmgwWeyJmcm9tIjowLCJ0byI6MTAwMDAwfQ&localPriority=0&q=%D0%BC%D0%BE%D1%82%D0%BE%D1%86%D0%B8%D0%BA%D0%BB%D1%8B&radius=100&searchRadius=100"
CHECK_INTERVAL = 300  # 5 минут

# ==================================================
# 2. НАСТРОЙКА ЛОГИРОВАНИЯ И БОТА
# ==================================================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================================================
# 3. РАБОТА С БАЗОЙ ДАННЫХ
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
# 4. СТРУКТУРА И ПАРСИНГ ОБЪЯВЛЕНИЙ
# ==================================================
@dataclass
class Ad:
    id: str
    title: str
    price: str
    url: str
    image_url: Optional[str] = None

def fetch_ads(search_url: str) -> List[Ad]:
    """Парсит все страницы поиска и возвращает все объявления"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    all_ads = []
    page = 1
    max_pages = 10  # Ограничиваем 10 страницами, чтобы не грузить Avito
    
    while page <= max_pages:
        # Формируем URL с номером страницы
        if "?" in search_url:
            page_url = search_url + f"&p={page}"
        else:
            page_url = search_url + f"?p={page}"
        
        logging.info(f"Парсинг страницы {page}...")
        
        try:
            response = requests.get(page_url, headers=headers, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            logging.error(f"Ошибка при запросе страницы {page}: {e}")
            break
        
        soup = BeautifulSoup(response.text, "html.parser")
        ad_items = soup.find_all("div", class_="iva-item-content")
        
        # Если на странице нет объявлений — выходим
        if not ad_items:
            logging.info(f"Страница {page} пуста, завершаем парсинг")
            break
        
        # Парсим объявления на странице
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
                all_ads.append(Ad(id=ad_id, title=title, price=price, url=ad_url, image_url=image_url))
            except Exception as e:
                logging.error(f"Ошибка при парсинге объявления на странице {page}: {e}")
                continue
        
        # Если объявлений меньше 50 — это последняя страница
        if len(ad_items) < 50:
            logging.info(f"На странице {page} всего {len(ad_items)} объявлений, это последняя страница")
            break
        
        page += 1
        # Небольшая задержка между страницами, чтобы не заблокировали
        time.sleep(1)
    
    logging.info(f"Всего найдено {len(all_ads)} объявлений на {page} страницах")
    return all_ads

# ==================================================
# 5. ОТПРАВКА УВЕДОМЛЕНИЙ
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
# 6. КОМАНДЫ БОТА
# ==================================================
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer("🚀 Бот запущен и отслеживает новые объявления на Avito.\nОжидайте уведомления!")

@dp.message(Command("status"))
async def status_command(message: types.Message):
    await message.answer(f"📡 Отслеживается URL:\n{SEARCH_URL}")

# ==================================================
# 7. ЗАГЛУШКА ДЛЯ RENDER (ВЕБ-СЕРВЕР)
# ==================================================
async def health_check(request):
    return web.Response(text="OK")

# ==================================================
# 8. ГЛАВНАЯ ФУНКЦИЯ
# ==================================================
async def main():
    init_db()
    logging.info("✅ Бот запущен...")

    # Запускаем фоновую проверку объявлений
    asyncio.create_task(check_new_ads())

    # --- ЗАГЛУШКА ДЛЯ RENDER, ЧТОБЫ НЕ БЫЛО ОШИБКИ "No open ports" ---
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    logging.info("🌐 Веб-заглушка запущена на порту 10000")
    # ------------------------------------------------------------------

    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
