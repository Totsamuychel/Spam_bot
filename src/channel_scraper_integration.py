#!/usr/bin/env python3
"""
Интеграция скрайпера каналов Telegram в основной бот
Позволяет собирать участников каналов для рассылки
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, 
    ChannelPrivateError, 
    ChatAdminRequiredError,
    UserPrivacyRestrictedError
)

class TelegramChannelScraper:
    """Интеграция скрайпера в основной бот"""
    
    def __init__(self, api_id: int, api_hash: str, session_name: str = "channel_scraper"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.logger = logging.getLogger(__name__)
        self.users_data = []
        
        # Настройки сбора
        self.DELAY_BETWEEN_USERS = 0.1  # Задержка между пользователями
        self.MAX_USERS_PER_CHANNEL = 10000  # Максимум пользователей с канала
        
    async def scrape_channel_to_json(self, 
                                   channel_username: str,
                                   output_file: str = "data/messages_data.json",
                                   message_text: str = "Ваше сообщение здесь") -> bool:
        """Собрать участников канала и сохранить в JSON"""
        client = None
        try:
            # Очищаем предыдущие данные
            self.users_data = []
            
            # Подключение к Telegram
            client = TelegramClient(self.session_name, self.api_id, self.api_hash)
            await asyncio.wait_for(client.connect(), timeout=15.0)
            
            if not await client.is_user_authorized():
                self.logger.error("❌ Не авторизован для сбора канала")
                return False
            
            # Получение канала
            self.logger.info(f"🔍 Поиск канала: {channel_username}")
            channel_username = channel_username.lstrip('@')  # Убираем @ если есть
            
            try:
                channel = await asyncio.wait_for(
                    client.get_entity(channel_username), 
                    timeout=10.0
                )
            except Exception as e:
                self.logger.error(f"❌ Канал {channel_username} не найден: {e}")
                return False
            
            self.logger.info(f"✅ Канал найден: {channel.title}")
            self.logger.info(f"📊 ID канала: {channel.id}")
            
            # Получение участников с ограничениями
            self.logger.info(f"📥 Начинаю сбор участников...")
            
            try:
                participants = await asyncio.wait_for(
                    client.get_participants(channel, limit=self.MAX_USERS_PER_CHANNEL),
                    timeout=60.0
                )
            except ChatAdminRequiredError:
                self.logger.error("❌ Требуются права администратора для просмотра участников")
                return False
            except Exception as e:
                self.logger.error(f"❌ Ошибка получения участников: {e}")
                return False
            
            self.logger.info(f"📋 Получено {len(participants)} участников")
            
            # Обработка участников
            processed_count = 0
            skipped_count = 0
            
            for i, participant in enumerate(participants):
                try:
                    # Фильтруем ботов и удаленные аккаунты
                    if getattr(participant, 'bot', False):
                        skipped_count += 1
                        continue
                    
                    if getattr(participant, 'deleted', False):
                        skipped_count += 1
                        continue
                    
                    # Собираем данные пользователя
                    user_data = {
                        "user_id": participant.id,
                        "username": f"@{participant.username}" if participant.username else None,
                        "display_name": self._get_display_name(participant),
                        "phone": getattr(participant, 'phone', None)
                    }
                    
                    # Удаляем None значения для чистоты JSON
                    user_data = {k: v for k, v in user_data.items() if v is not None}
                    
                    self.users_data.append(user_data)
                    processed_count += 1
                    
                    # Прогресс каждые 100 пользователей
                    if processed_count % 100 == 0:
                        self.logger.info(f"📊 Обработано: {processed_count}/{len(participants)}")
                    
                    # Задержка для избежания FloodWait
                    await asyncio.sleep(self.DELAY_BETWEEN_USERS)
                    
                except Exception as e:
                    self.logger.warning(f"⚠️ Ошибка обработки пользователя {i}: {e}")
                    skipped_count += 1
                    continue
            
            self.logger.info(f"✅ Обработано: {processed_count}, пропущено: {skipped_count}")
            
            # Сохранение в JSON
            success = await self._save_to_json(output_file, message_text)
            
            await client.disconnect()
            return success
            
        except FloodWaitError as e:
            self.logger.error(f"⏳ FloodWait: {e.seconds}с. Увеличьте DELAY_BETWEEN_USERS")
            return False
        except ChannelPrivateError:
            self.logger.error("🔒 Канал приватный или недоступен")
            return False
        except asyncio.TimeoutError:
            self.logger.error("⏰ Таймаут при сборе данных канала")
            return False
        except Exception as e:
            self.logger.error(f"💥 Критическая ошибка сбора канала: {e}", exc_info=True)
            return False
        finally:
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except:
                    pass
    
    async def _save_to_json(self, output_file: str, message_text: str) -> bool:
        """Сохранить данные в JSON формате совместимом с message_queue"""
        try:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Создаем структуру совместимую с message_queue
            data = {
                "message": message_text,
                "recipients": self.users_data,
                "metadata": {
                    "total_users": len(self.users_data),
                    "collection_timestamp": asyncio.get_event_loop().time(),
                    "scraper_version": "1.0"
                }
            }
            
            # Сохраняем с красивым форматированием
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            self.logger.info(f"💾 Сохранено {len(self.users_data)} пользователей в {output_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка сохранения в JSON: {e}", exc_info=True)
            return False
    
    async def get_channel_info(self, channel_username: str) -> Optional[Dict]:
        """Получить информацию о канале без сбора участников"""
        client = None
        try:
            client = TelegramClient(f"{self.session_name}_info", self.api_id, self.api_hash)
            await client.connect()
            
            if not await client.is_user_authorized():
                return None
            
            channel_username = channel_username.lstrip('@')
            channel = await client.get_entity(channel_username)
            
            info = {
                'id': channel.id,
                'title': channel.title,
                'username': getattr(channel, 'username', None),
                'participants_count': getattr(channel, 'participants_count', 0),
                'description': getattr(channel, 'about', None),
                'is_megagroup': getattr(channel, 'megagroup', False),
                'is_broadcast': getattr(channel, 'broadcast', False)
            }
            
            await client.disconnect()
            return info
            
        except Exception as e:
            self.logger.error(f"Ошибка получения информации о канале: {e}")
            return None
        finally:
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except:
                    pass
    
    @staticmethod
    def _get_display_name(user) -> Optional[str]:
        """Получить отображаемое имя пользователя"""
        name_parts = []
        
        if hasattr(user, 'first_name') and user.first_name:
            name_parts.append(user.first_name)
        
        if hasattr(user, 'last_name') and user.last_name:
            name_parts.append(user.last_name)
        
        if name_parts:
            return ' '.join(name_parts)
        
        if hasattr(user, 'username') and user.username:
            return f"@{user.username}"
        
        return None
    
    def get_stats(self) -> Dict:
        """Получить статистику последнего сбора"""
        return {
            'total_users': len(self.users_data),
            'users_with_username': len([u for u in self.users_data if u.get('username')]),
            'users_with_phone': len([u for u in self.users_data if u.get('phone')]),
            'users_with_display_name': len([u for u in self.users_data if u.get('display_name')])
        }
    
    def set_collection_settings(self, delay: float = 0.1, max_users: int = 10000):
        """Настроить параметры сбора"""
        self.DELAY_BETWEEN_USERS = delay
        self.MAX_USERS_PER_CHANNEL = max_users
        self.logger.info(f"⚙️ Настройки сбора: задержка={delay}с, макс_пользователей={max_users}")