#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Channel Scraper - Single JSON Output
Скрипт для сбора участников канала с сохранением в один JSON файл
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, 
    ChannelPrivateError, 
    ChatAdminRequiredError,
    UserPrivacyRestrictedError,
    SessionPasswordNeededError
)
from telethon.tl.types import User

# =============================================================================
# НАСТРОЙКИ СКРИПТА
# =============================================================================

# Используем существующие настройки
try:
    from telegram_config import API_ID, API_HASH, SESSION_NAME
    print("✅ Настройки загружены из telegram_config.py")
except ImportError:
    print("⚠️  Файл telegram_config.py не найден, используются настройки по умолчанию")
    API_ID = '29575527'
    API_HASH = 'fcb798565cffe3640172fef9082adb72'
    SESSION_NAME = 'telegram_session'

# Настройки для нового скрипта
CHANNEL_URL = 'https://t.me/ewrfdgf21'  # Целевой канал
CHANNEL_USERNAME = '@ewrfdgf21'         # Username канала
OUTPUT_FILE = 'output/users.json'       # Путь к выходному JSON файлу
LOG_FILE = 'scraper.log'                # Файл логов

# Настройки производительности
DELAY_BETWEEN_USERS = 0.1  # Задержка между пользователями
MAX_FLOOD_WAIT = 300       # Максимальное ожидание FloodWaitError

# =============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# =============================================================================

def setup_logging():
    """Настройка системы логирования"""
    formatter = logging.Formatter(
        '[%(levelname)s] %(asctime)s — %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Логирование в файл
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    
    # Логирование в консоль
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Настройка основного логгера
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# =============================================================================
# ОСНОВНОЙ КЛАСС СКРАПЕРА
# =============================================================================

class TelegramChannelScraperSingleJSON:
    """Класс для сбора участников канала в один JSON файл"""
    
    def __init__(self, api_id: str, api_hash: str, session_name: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.client = None
        self.logger = logging.getLogger()
        self.users_data = []  # Список для хранения всех пользователей
        self.processed_count = 0
        self.error_count = 0
    
    async def connect(self) -> bool:
        """Подключение к Telegram API"""
        try:
            self.logger.info("Подключение к Telegram API...")
            self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
            await self.client.start()
            
            if not await self.client.is_user_authorized():
                self.logger.error("Пользователь не авторизован. Запустите авторизацию.")
                return False
            
            me = await self.client.get_me()
            self.logger.info(f"Успешно подключен как: {me.first_name} (@{me.username})")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка подключения к Telegram API: {e}")
            return False
    
    def normalize_user_data(self, user: User) -> Dict[str, Any]:
        """Нормализация данных пользователя"""
        return {
            "user_id": user.id,
            "username": user.username if user.username else None,
            "display_name": self._get_display_name(user),
            "phone": user.phone if hasattr(user, 'phone') and user.phone else None
        }
    
    def _get_display_name(self, user: User) -> Optional[str]:
        """Получение отображаемого имени пользователя"""
        if user.first_name and user.last_name:
            return f"{user.first_name} {user.last_name}"
        elif user.first_name:
            return user.first_name
        elif user.last_name:
            return user.last_name
        else:
            return None
    
    def save_all_users_to_json(self, output_file: str) -> bool:
        """Сохранение всех пользователей в один JSON файл"""
        try:
            # Создаем папку если её нет
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Сохраняем все данные в один файл
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(self.users_data, f, ensure_ascii=False, indent=2)
            
            self.logger.info(f"Все пользователи сохранены в файл: {output_file}")
            self.logger.info(f"Общее количество пользователей: {len(self.users_data)}")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка сохранения файла {output_file}: {e}")
            return False
    
    async def get_channel_members(self, channel_username: str, output_file: str):
        """Получение участников канала и сохранение в JSON файл"""
        try:
            # Получаем информацию о канале
            self.logger.info(f"Получение информации о канале {channel_username}...")
            channel = await self.client.get_entity(channel_username)
            self.logger.info(f"Канал найден: {channel.title}")
            
            # Получаем участников канала
            self.logger.info("Получение списка участников...")
            participants = await self.client.get_participants(channel)
            total_participants = len(participants)
            self.logger.info(f"Получено {total_participants} участников.")
            
            # Обрабатываем каждого участника
            for i, participant in enumerate(participants, 1):
                try:
                    if isinstance(participant, User):
                        # Нормализуем данные пользователя
                        user_data = self.normalize_user_data(participant)
                        
                        # Добавляем в общий список
                        self.users_data.append(user_data)
                        self.processed_count += 1
                        
                        # Показываем прогресс каждые 50 пользователей
                        if i % 50 == 0:
                            self.logger.info(f"Обработано {i}/{total_participants} участников")
                        
                        # Небольшая задержка между запросами
                        await asyncio.sleep(DELAY_BETWEEN_USERS)
                    
                except FloodWaitError as e:
                    wait_time = min(e.seconds, MAX_FLOOD_WAIT)
                    self.logger.warning(f"FloodWaitError: ожидание {wait_time} секунд...")
                    await asyncio.sleep(wait_time)
                    continue
                    
                except UserPrivacyRestrictedError:
                    self.logger.warning(f"Пользователь {participant.id} ограничил доступ к данным")
                    self.error_count += 1
                    continue
                    
                except Exception as e:
                    self.logger.error(f"Ошибка при обработке user_id {participant.id}: {e}")
                    self.error_count += 1
                    continue
            
            # Сохраняем всех пользователей в один файл
            if self.save_all_users_to_json(output_file):
                self.logger.info(f"✅ Успешно сохранено {self.processed_count} пользователей в {output_file}")
            else:
                self.logger.error("❌ Ошибка при сохранении файла")
                
            if self.error_count > 0:
                self.logger.warning(f"Ошибок при обработке: {self.error_count}")
                
        except ChannelPrivateError:
            self.logger.error("Канал приватный или недоступен")
        except ChatAdminRequiredError:
            self.logger.error("Требуются права администратора для доступа к участникам")
        except Exception as e:
            self.logger.error(f"Ошибка при получении участников канала: {e}")
    
    async def disconnect(self):
        """Отключение от Telegram API"""
        if self.client:
            await self.client.disconnect()
            self.logger.info("Отключение от Telegram API")
    
    def print_summary(self):
        """Вывод итоговой статистики"""
        print("\n" + "=" * 60)
        print("📊 ИТОГОВАЯ СТАТИСТИКА")
        print("=" * 60)
        print(f"👥 Всего обработано пользователей: {self.processed_count}")
        print(f"❌ Ошибок при обработке: {self.error_count}")
        print(f"📁 Файл сохранен: {OUTPUT_FILE}")
        print(f"📝 Логи записаны в: {LOG_FILE}")
        
        if self.users_data:
            # Статистика по данным
            users_with_username = sum(1 for user in self.users_data if user.get('username'))
            users_with_display_name = sum(1 for user in self.users_data if user.get('display_name'))
            users_with_phone = sum(1 for user in self.users_data if user.get('phone'))
            
            print(f"🏷️  С username: {users_with_username} ({users_with_username/len(self.users_data)*100:.1f}%)")
            print(f"📝 С отображаемым именем: {users_with_display_name} ({users_with_display_name/len(self.users_data)*100:.1f}%)")
            print(f"📞 С номером телефона: {users_with_phone} ({users_with_phone/len(self.users_data)*100:.1f}%)")
        
        print("=" * 60)

# =============================================================================
# ОСНОВНАЯ ФУНКЦИЯ
# =============================================================================

async def main():
    """Основная функция скрипта"""
    # Настройка логирования
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Запуск Telegram Channel Scraper (Single JSON)")
    logger.info("=" * 60)
    logger.info(f"Целевой канал: {CHANNEL_URL}")
    logger.info(f"Выходной файл: {OUTPUT_FILE}")
    
    # Создание экземпляра скрапера
    scraper = TelegramChannelScraperSingleJSON(API_ID, API_HASH, SESSION_NAME)
    
    try:
        # Подключение к Telegram API
        if not await scraper.connect():
            logger.error("Не удалось подключиться к Telegram API")
            return
        
        # Получение участников канала
        await scraper.get_channel_members(CHANNEL_USERNAME, OUTPUT_FILE)
        
        # Вывод итоговой статистики
        scraper.print_summary()
        
    except KeyboardInterrupt:
        logger.info("Скрипт прерван пользователем")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
    finally:
        # Отключение от API
        await scraper.disconnect()
        logger.info("Скрипт завершён")

# =============================================================================
# ТОЧКА ВХОДА
# =============================================================================

if __name__ == "__main__":
    print("🚀 Telegram Channel Scraper - Single JSON Output")
    print(f"📺 Канал: {CHANNEL_URL}")
    print(f"📁 Выходной файл: {OUTPUT_FILE}")
    print("=" * 60)
    
    # Запуск асинхронной функции
    asyncio.run(main())