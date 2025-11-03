#!/usr/bin/env python3
"""
Модуль сбора участников Telegram-группы/канала двумя способами
Способ 1: По прямому username или ссылке
Способ 2: По названию публичной группы/канала с поиском
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User
from telethon.errors import (
    FloodWaitError, 
    ChannelPrivateError, 
    ChatAdminRequiredError,
    UserPrivacyRestrictedError
)


class MemberCollector:
    """Класс для сбора участников Telegram-групп/каналов"""
    
    def __init__(self, api_id: int, api_hash: str, session_name: str = "member_collector"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.logger = logging.getLogger(__name__)
        self.users_data = []
        
        # Настройки сбора
        self.DELAY_BETWEEN_USERS = 0.1
        self.MAX_USERS_PER_CHANNEL = 10000
        self.SEARCH_LIMIT = 50  # Лимит поиска публичных групп
        self.BATCH_SIZE = 100  # Размер батча для пагинации
        
        # Адаптивные задержки
        self.base_delay = 0.2
        self.max_delay = 1.0
        self.current_rate_limit = 10  # запросов в секунду
        
        # Ссылка на внешний авторизованный клиент (если есть)
        self.external_client = None
    
    def set_external_client(self, client):
        """Установить внешний авторизованный клиент"""
        self.external_client = client
        self.logger.info("Установлен внешний авторизованный клиент")
    
    def _get_adaptive_delay(self) -> float:
        """Вычисляет адаптивную задержку на основе текущего rate limit"""
        adaptive_delay = min(self.base_delay, self.max_delay / self.current_rate_limit)
        return max(0.1, adaptive_delay)  # Минимум 0.1 секунды
    
    async def _async_input(self, prompt: str) -> str:
        """Неблокирующий ввод с поддержкой aioconsole"""
        try:
            import aioconsole
            return await aioconsole.ainput(prompt)
        except ImportError:
            # Fallback к обычному input (может блокировать)
            return input(prompt)
    
    async def collect_members_menu(self) -> bool:
        """Главное меню выбора способа сбора участников"""
        print("\n" + "="*60)
        print("📥 СБОР УЧАСТНИКОВ TELEGRAM-ГРУППЫ/КАНАЛА")
        print("="*60)
        print("Выберите способ сбора участников:")
        print("1. По username или ссылке")
        print("2. По названию публичной группы/канала")
        print("0. Отмена")
        print("="*60)
        
        while True:
            try:
                choice = await self._async_input("Ваш выбор (1/2/0): ")
                choice = choice.strip()
                
                if choice == '1':
                    return await self.collect_by_username()
                elif choice == '2':
                    return await self.collect_by_display_name()
                elif choice == '0':
                    print("❌ Сбор отменен")
                    return False
                else:
                    print("❌ Неверный выбор. Введите 1, 2 или 0")
                    
            except KeyboardInterrupt:
                print("\n❌ Операция прервана пользователем")
                return False
    
    async def collect_by_username(self) -> bool:
        """Способ 1: Сбор участников по прямому username или ссылке"""
        print("\n📋 СБОР ПО USERNAME ИЛИ ССЫЛКЕ")
        print("-" * 40)
        print("Поддерживаемые форматы:")
        print("• Ссылка: https://t.me/channelname")
        print("• Ссылка: t.me/channelname")
        print("• Username: @channelname")
        print("• Username: channelname")
        
        while True:
            try:
                channel_input = await self._async_input("\n🔗 Введите ссылку на канал или его username: ")
                channel_input = channel_input.strip()
                
                if not channel_input:
                    print("❌ Ввод не может быть пустым")
                    continue
                
                if not self.validate_channel_input(channel_input):
                    print("❌ Неверный формат. Попробуйте еще раз.")
                    continue
                
                return await self._collect_members_from_channel(channel_input)
                
            except KeyboardInterrupt:
                print("\n❌ Операция прервана пользователем")
                return False
    
    async def collect_by_display_name(self) -> bool:
        """Способ 2: Сбор участников по названию, username или ссылке группы/канала"""
        print("\n🔍 СБОР ПО НАЗВАНИЮ, USERNAME ИЛИ ССЫЛКЕ")
        print("-" * 50)
        print("Поддерживаемые форматы ввода:")
        print("• Название: Бензин в Самарканде")
        print("• Username: @benzin_samarkand")
        print("• Ссылка: https://t.me/benzin_samarkand")
        print("• Ссылка: t.me/benzin_samarkand")
        
        while True:
            try:
                user_input = await self._async_input("\n📝 Введите название, юзернейм (@имя) или ссылку группы/канала: ")
                user_input = user_input.strip()
                
                if not user_input:
                    print("❌ Ввод не может быть пустым")
                    continue
                
                if len(user_input) < 3:
                    print("❌ Ввод слишком короткий (минимум 3 символа)")
                    continue
                
                # Определяем тип ввода и обрабатываем напрямую без рекурсии
                input_type = self._detect_input_type(user_input)
                print(f"🔍 Обнаружен тип ввода: {input_type}")
                
                if input_type == "username_or_link":
                    # Если это username или ссылка - сразу парсим
                    return await self._collect_by_username_or_link(user_input)
                else:
                    # Если это название - ищем по display name
                    return await self.collect_members_by_display_name(user_input)
                
            except KeyboardInterrupt:
                print("\n❌ Операция прервана пользователем")
                return False
    
    async def collect_members_by_input(self, user_input: str) -> bool:
        """Обработка различных типов ввода: название, username или ссылка"""
        # Определяем тип ввода
        input_type = self._detect_input_type(user_input)
        
        print(f"🔍 Обнаружен тип ввода: {input_type}")
        
        if input_type == "username_or_link":
            # Если это username или ссылка - сразу парсим
            return await self._collect_by_username_or_link(user_input)
        else:
            # Если это название - ищем по display name
            return await self.collect_members_by_display_name(user_input)
    
    async def _collect_by_username_or_link(self, user_input: str) -> bool:
        """Сбор участников по username или ссылке"""
        client = None
        client_owned = False
        
        try:
            print(f"🔗 Обработка username/ссылки: {user_input}")
            
            # Извлекаем username из ввода
            username = self._extract_channel_username(user_input)
            print(f"📝 Извлеченный username: {username}")
            
            # Используем внешний клиент, если доступен
            if self.external_client:
                client = self.external_client
                print("🔐 Используется авторизованный клиент из основного бота")
            else:
                # Создаем собственное подключение
                client = TelegramClient(self.session_name, self.api_id, self.api_hash)
                await client.connect()
                client_owned = True
                
                if not await client.is_user_authorized():
                    print("❌ Не авторизован для сбора участников")
                    print("💡 Подсказка: Сначала авторизуйтесь через основной бот (пункт 3 в меню)")
                    return False
            
            # Получаем сущность канала/группы
            try:
                entity = await client.get_entity(username)
                print(f"✅ Найдена группа/канал: {getattr(entity, 'title', 'Без названия')}")
                
                # Показываем информацию о найденной группе/канале
                await self._show_entity_info(entity)
                
                # Подтверждение от пользователя
                try:
                    confirm = await self._async_input(f"\nСобрать участников из этой группы/канала? (y/n): ")
                    confirm = confirm.strip().lower()
                    if confirm != 'y':
                        print("❌ Сбор отменен пользователем")
                        await client.disconnect()
                        return False
                except KeyboardInterrupt:
                    print("\n❌ Операция прервана пользователем")
                    await client.disconnect()
                    return False
                
            except Exception as e:
                print(f"❌ Группа/канал '{username}' не найдена или недоступна: {e}")
                await client.disconnect()
                return False
            
            # Собираем участников
            success = await self._collect_members_from_entity(client, entity)
            
            # Отключаем только если создали клиент сами
            if client_owned and client:
                await client.disconnect()
            return success
            
        except Exception as e:
            self.logger.error(f"Ошибка сбора по username/ссылке: {e}", exc_info=True)
            print(f"❌ Ошибка: {e}")
            return False
        finally:
            # Отключаем только если создали клиент сами
            if client_owned and client and client.is_connected():
                try:
                    await client.disconnect()
                except:
                    pass
    
    def _detect_input_type(self, user_input: str) -> str:
        """Определение типа ввода: username/ссылка или название"""
        user_input = user_input.strip()
        
        # Проверяем на ссылки
        if any(pattern in user_input.lower() for pattern in ['t.me/', 'telegram.me/']):
            return "username_or_link"
        
        # Проверяем на username (начинается с @)
        if user_input.startswith('@'):
            return "username_or_link"
        
        # Проверяем на простой username без @ (только латинские буквы, цифры, подчеркивания)
        # Telegram username: 5-32 символа, начинается с буквы, может содержать буквы, цифры и подчеркивания
        if re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', user_input):
            return "username_or_link"
        
        # Если ничего не подошло - считаем названием
        return "display_name"
    
    async def _show_entity_info(self, entity) -> None:
        """Показать информацию о найденной группе/канале"""
        print("\n📋 ИНФОРМАЦИЯ О ГРУППЕ/КАНАЛЕ:")
        print("-" * 40)
        
        # Название
        title = getattr(entity, 'title', 'Без названия')
        print(f"📺 Название: {title}")
        
        # Username
        username = getattr(entity, 'username', None)
        if username:
            print(f"🏷️ Username: @{username}")
        
        # Тип
        if hasattr(entity, 'broadcast') and entity.broadcast:
            entity_type = "Канал"
        elif hasattr(entity, 'megagroup') and entity.megagroup:
            entity_type = "Супергруппа"
        else:
            entity_type = "Группа"
        print(f"🏢 Тип: {entity_type}")
        
        # Количество участников
        participants_count = getattr(entity, 'participants_count', 0)
        if participants_count:
            print(f"👥 Участников: {participants_count}")
        
        # Описание (если есть)
        description = getattr(entity, 'about', None)
        if description and len(description) > 0:
            preview = description[:100] + "..." if len(description) > 100 else description
            print(f"📝 Описание: {preview}")
        
        print("-" * 40)

    async def collect_members_by_display_name(self, display_name: str) -> bool:
        """Поиск и сбор участников по названию группы/канала"""
        client = None
        client_owned = False  # Флаг, указывающий, создали ли мы клиент сами
        
        try:
            # Используем внешний клиент, если доступен
            if self.external_client:
                client = self.external_client
                print("🔐 Используется авторизованный клиент из основного бота")
            else:
                # Создаем собственное подключение
                client = TelegramClient(self.session_name, self.api_id, self.api_hash)
                await client.connect()
                client_owned = True
                
                if not await client.is_user_authorized():
                    print("❌ Не авторизован для поиска групп")
                    print("💡 Подсказка: Сначала авторизуйтесь через основной бот (пункт 3 в меню)")
                    return False
            
            # Цикл для повторного ввода при неудачном поиске
            current_query = display_name
            while True:
                print(f"\n🔍 Анализ запроса: '{current_query}'")
                
                # НОВАЯ ЛОГИКА: Сначала проверяем, не является ли это username/ссылкой
                input_type = self._detect_input_type(current_query)
                
                if input_type == "username_or_link":
                    print("🔗 Обнаружена ссылка или username - попытка прямого доступа")
                    
                    # Извлекаем username
                    username = self._extract_channel_username(current_query)
                    print(f"📝 Извлеченный username: {username}")
                    
                    try:
                        # Пытаемся получить сущность напрямую (работает для публичных групп/каналов)
                        entity = await client.get_entity(username)
                        print(f"✅ Найдена публичная группа/канал: {getattr(entity, 'title', 'Без названия')}")
                        
                        # Показываем информацию о найденной группе/канале
                        await self._show_entity_info(entity)
                        
                        # Подтверждение от пользователя
                        confirm = await self._async_input(f"\nСобрать участников из этой группы/канала? (y/n): ")
                        confirm = confirm.strip().lower()
                        if confirm == 'y':
                            # Собираем участников напрямую
                            print(f"\n📥 Начинаем сбор участников из '{getattr(entity, 'title', username)}'...")
                            success = await self._collect_members_from_entity(client, entity)
                            
                            # Отключаем только если создали клиент сами
                            if client_owned and client:
                                await client.disconnect()
                            return success
                        else:
                            print("❌ Сбор отменен пользователем")
                            # Предлагаем ввести другой запрос
                            retry = await self._async_input("Попробовать с другим запросом? (y/n): ")
                            if retry.strip().lower() == 'y':
                                new_query = await self._async_input("\n📝 Введите новый запрос: ")
                                new_query = new_query.strip()
                                if new_query and len(new_query) >= 3:
                                    current_query = new_query
                                    continue
                            
                            # Отключаем только если создали клиент сами
                            if client_owned and client:
                                await client.disconnect()
                            return False
                            
                    except Exception as e:
                        print(f"⚠️ Не удалось получить доступ к '{username}': {e}")
                        print("💡 Возможные причины:")
                        print("   • Группа/канал приватные")
                        print("   • Неверный username")
                        print("   • Группа/канал не существует")
                        
                        # Предлагаем попробовать поиск по названию или ввести новый запрос
                        print("\n🔄 Варианты действий:")
                        print("1. Попробовать поиск по названию")
                        print("2. Ввести другой username/ссылку")
                        print("3. Отмена")
                        
                        choice = await self._async_input("Ваш выбор (1/2/3): ")
                        choice = choice.strip()
                        
                        if choice == '1':
                            # Переходим к поиску по названию (используем текущий запрос как название)
                            input_type = "display_name"
                            print("🔍 Переключаемся на поиск по названию...")
                        elif choice == '2':
                            new_query = await self._async_input("\n📝 Введите новый username или ссылку: ")
                            new_query = new_query.strip()
                            if new_query and len(new_query) >= 3:
                                current_query = new_query
                                continue
                            else:
                                print("❌ Некорректный ввод")
                                continue
                        else:
                            # Отключаем только если создали клиент сами
                            if client_owned and client:
                                await client.disconnect()
                            return False
                
                # Если это название или если прямой доступ не удался - ищем по display name
                if input_type == "display_name":
                    print(f"🔍 Поиск публичных групп/каналов по названию: '{current_query}'")
                    
                    # Поиск публичных групп/каналов среди диалогов
                    found_chats = await self._search_public_chats(client, current_query)
                    
                    if not found_chats:
                        print(f"❌ Не найдено публичных групп/каналов по запросу '{current_query}'")
                        retry = await self._async_input("Попробовать с другим названием? (y/n): ")
                        retry = retry.strip().lower()
                        
                        if retry == 'y':
                            # Запрашиваем новое название вместо рекурсии
                            new_query = await self._async_input("\n📝 Введите новое название, username или ссылку: ")
                            new_query = new_query.strip()
                            
                            if not new_query or len(new_query) < 3:
                                print("❌ Запрос слишком короткий или пустой")
                                continue
                            
                            current_query = new_query
                            continue
                        else:
                            # Отключаем только если создали клиент сами
                            if client_owned and client:
                                await client.disconnect()
                            return False
                    
                    # Показываем найденные варианты
                    selected_chat = await self._select_chat_from_results(found_chats)
                    
                    if not selected_chat:
                        print("❌ Группа/канал не выбрана")
                        # Предлагаем попробовать снова вместо выхода
                        retry = await self._async_input("Попробовать поиск заново? (y/n): ")
                        if retry.strip().lower() == 'y':
                            continue
                        else:
                            # Отключаем только если создали клиент сами
                            if client_owned and client:
                                await client.disconnect()
                            return False
                    
                    # Собираем участников выбранной группы/канала
                    print(f"\n📥 Начинаем сбор участников из '{selected_chat['title']}'...")
                    success = await self._collect_members_from_entity(client, selected_chat['entity'])
                
                # Отключаем только если создали клиент сами
                if client_owned and client:
                    await client.disconnect()
                return success
            
        except Exception as e:
            self.logger.error(f"Ошибка при сборе по названию: {e}", exc_info=True)
            print(f"❌ Ошибка: {e}")
            return False
        finally:
            # Отключаем только если создали клиент сами
            if client_owned and client and client.is_connected():
                try:
                    await client.disconnect()
                except:
                    pass
    
    async def _search_public_chats(self, client: TelegramClient, query: str) -> List[Dict]:
        """Поиск публичных чатов по названию"""
        found_chats = []
        
        try:
            print("🔄 Выполняется поиск...")
            
            # Получаем диалоги пользователя
            dialogs = await client.get_dialogs(limit=self.SEARCH_LIMIT)
            
            # Фильтруем по названию (регистронезависимый поиск)
            query_lower = query.lower()
            
            for dialog in dialogs:
                entity = dialog.entity
                
                # Проверяем только каналы и группы
                if isinstance(entity, (Channel, Chat)):
                    title = getattr(entity, 'title', '')
                    
                    if title and query_lower in title.lower():
                        # Проверяем что это публичная группа/канал
                        if isinstance(entity, Channel):
                            # Для каналов проверяем наличие username (публичные)
                            if hasattr(entity, 'username') and entity.username:
                                chat_info = {
                                    'entity': entity,
                                    'title': title,
                                    'username': entity.username,
                                    'type': 'Канал' if getattr(entity, 'broadcast', False) else 'Группа',
                                    'participants_count': getattr(entity, 'participants_count', 0),
                                    'is_public': True
                                }
                                found_chats.append(chat_info)
                        elif isinstance(entity, Chat):
                            # Обычные группы (всегда публичные если мы в них состоим)
                            chat_info = {
                                'entity': entity,
                                'title': title,
                                'username': None,
                                'type': 'Группа',
                                'participants_count': getattr(entity, 'participants_count', 0),
                                'is_public': True
                            }
                            found_chats.append(chat_info)
            
            # Дополнительно пытаемся найти через глобальный поиск
            try:
                # Поиск через search_global (если доступен)
                global_results = await client.get_dialogs(limit=200)
                
                for dialog in global_results:
                    entity = dialog.entity
                    if isinstance(entity, Channel) and hasattr(entity, 'username') and entity.username:
                        title = getattr(entity, 'title', '')
                        if title and query_lower in title.lower():
                            # Проверяем что мы еще не добавили этот канал
                            if not any(chat['username'] == entity.username for chat in found_chats if chat['username']):
                                chat_info = {
                                    'entity': entity,
                                    'title': title,
                                    'username': entity.username,
                                    'type': 'Канал' if getattr(entity, 'broadcast', False) else 'Группа',
                                    'participants_count': getattr(entity, 'participants_count', 0),
                                    'is_public': True
                                }
                                found_chats.append(chat_info)
                                
            except Exception as e:
                self.logger.debug(f"Глобальный поиск недоступен: {e}")
            
            return found_chats
            
        except Exception as e:
            self.logger.error(f"Ошибка поиска чатов: {e}")
            return []
    
    async def _select_chat_from_results(self, found_chats: List[Dict]) -> Optional[Dict]:
        """Выбор чата из найденных результатов"""
        if len(found_chats) == 1:
            chat = found_chats[0]
            print(f"\n✅ Найдена одна группа/канал:")
            print(f"   📺 {chat['title']}")
            print(f"   🏷️ Тип: {chat['type']}")
            print(f"   👥 Участников: {chat['participants_count']}")
            if chat['username']:
                print(f"   🔗 Username: @{chat['username']}")
            
            confirm = await self._async_input(f"\nИспользовать '{chat['title']}'? (y/n): ")
            confirm = confirm.strip().lower()
            return chat if confirm == 'y' else None
        
        print(f"\n📋 Найдено {len(found_chats)} групп/каналов:")
        print("-" * 60)
        
        for i, chat in enumerate(found_chats, 1):
            print(f"{i}. 📺 {chat['title']}")
            print(f"   🏷️ Тип: {chat['type']}")
            print(f"   👥 Участников: {chat['participants_count']}")
            if chat['username']:
                print(f"   🔗 Username: @{chat['username']}")
            print("-" * 60)
        
        while True:
            try:
                choice = await self._async_input(f"\nВыберите группу/канал (1-{len(found_chats)}) или 0 для отмены: ")
                choice = choice.strip()
                
                if choice == '0':
                    return None
                
                index = int(choice) - 1
                if 0 <= index < len(found_chats):
                    return found_chats[index]
                else:
                    print(f"❌ Неверный выбор. Введите число от 1 до {len(found_chats)}")
                    
            except ValueError:
                print("❌ Введите корректное число")
            except KeyboardInterrupt:
                return None
    
    async def _collect_members_from_channel(self, channel_input: str) -> bool:
        """Сбор участников из канала по username/ссылке"""
        client = None
        client_owned = False
        
        try:
            # Используем внешний клиент, если доступен
            if self.external_client:
                client = self.external_client
                print("🔐 Используется авторизованный клиент из основного бота")
            else:
                # Создаем собственное подключение
                client = TelegramClient(self.session_name, self.api_id, self.api_hash)
                await client.connect()
                client_owned = True
                
                if not await client.is_user_authorized():
                    print("❌ Не авторизован для сбора участников")
                    print("💡 Подсказка: Сначала авторизуйтесь через основной бот (пункт 3 в меню)")
                    return False
            
            # Извлекаем username канала
            channel_username = self._extract_channel_username(channel_input)
            
            # Получаем сущность канала
            try:
                entity = await client.get_entity(channel_username)
            except Exception as e:
                print(f"❌ Канал '{channel_username}' не найден: {e}")
                return False
            
            # Собираем участников
            success = await self._collect_members_from_entity(client, entity)
            
            # Отключаем только если создали клиент сами
            if client_owned and client:
                await client.disconnect()
            return success
            
        except Exception as e:
            self.logger.error(f"Ошибка сбора по username: {e}", exc_info=True)
            print(f"❌ Ошибка: {e}")
            return False
        finally:
            # Отключаем только если создали клиент сами
            if client_owned and client and client.is_connected():
                try:
                    await client.disconnect()
                except:
                    pass
    
    async def _collect_members_from_entity(self, client: TelegramClient, entity) -> bool:
        """Сбор участников из конкретной сущности (канал/группа)"""
        try:
            # Очищаем предыдущие данные
            self.users_data = []
            
            # Получаем информацию о канале/группе
            title = getattr(entity, 'title', 'Неизвестно')
            participants_count = getattr(entity, 'participants_count', 0)
            
            print(f"📺 Канал/группа: {title}")
            print(f"👥 Участников: {participants_count}")
            
            # Пытаемся получить участников разными способами
            participants = await self._get_participants_with_fallback(client, entity)
            
            if not participants:
                print("❌ Не удалось получить список участников")
                await self._suggest_alternatives(entity, title)
                return False
            
            print(f"📋 Получено {len(participants)} участников для обработки")
            
            # Обрабатываем участников
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
                        print(f"📊 Обработано: {processed_count}/{len(participants)}")
                    
                    # Задержка для избежания FloodWait
                    await asyncio.sleep(self.DELAY_BETWEEN_USERS)
                    
                except Exception as e:
                    self.logger.warning(f"⚠️ Ошибка обработки пользователя {i}: {e}")
                    skipped_count += 1
                    continue
            
            print(f"✅ Обработано: {processed_count}, пропущено: {skipped_count}")
            
            # Детальный анализ результатов сбора
            await self._analyze_collection_results(processed_count, participants_count, title)
            
            # Сохраняем в JSON
            return await self._save_to_json()
            
        except FloodWaitError as e:
            print(f"⏳ FloodWait: {e.seconds}с. Попробуйте позже")
            return False
        except Exception as e:
            self.logger.error(f"Ошибка сбора участников: {e}", exc_info=True)
            print(f"❌ Ошибка: {e}")
            return False
    
    async def _get_participants_with_fallback(self, client: TelegramClient, entity) -> List:
        """
        Максимально эффективное получение участников публичных групп/каналов
        Использует агрессивные параметры и множественные методы для обхода лимитов Telegram
        """
        all_participants = {}  # Словарь для исключения дубликатов по user_id
        
        # Получаем информацию о канале для анализа
        total_count = getattr(entity, 'participants_count', 0)
        entity_type = "Канал" if getattr(entity, 'broadcast', False) else "Супергруппа" if getattr(entity, 'megagroup', False) else "Группа"
        
        print(f"📊 Анализ {entity_type}: {total_count} участников заявлено")
        
        # Предварительная оценка возможностей сбора
        await self._estimate_collection_potential(client, entity, total_count)
        
        # Метод 1: Агрессивный сбор с оптимальными параметрами
        try:
            print("🚀 Метод 1: Агрессивный сбор участников (aggressive=True)")
            self.logger.info(f"Начинаем агрессивный сбор участников для {entity_type} с {total_count} участниками")
            
            # Используем пагинацию для получения всех участников
            aggressive_participants = await self._get_all_participants_paginated(client, entity)
            
            for participant in aggressive_participants:
                all_participants[participant.id] = participant
            
            coverage_percent = (len(aggressive_participants) / total_count * 100) if total_count and total_count > 0 else 0
            
            print(f"✅ Агрессивный метод: получено {len(aggressive_participants)} участников")
            print(f"📊 Покрытие: {coverage_percent:.1f}% от заявленного количества")
            
            self.logger.info(f"Агрессивный сбор: {len(aggressive_participants)}/{total_count} участников ({coverage_percent:.1f}%)")
            
            # Анализируем качество покрытия
            if coverage_percent < 50 and total_count and total_count > 100:
                print("⚠️ Низкое покрытие! Telegram ограничивает доступ к полному списку участников")
                print("💡 Это техническое ограничение мессенджера для борьбы со спамом")
                use_additional_methods = True
            elif len(aggressive_participants) < 50:
                print("⚠️ Получено мало участников, применяем дополнительные методы...")
                use_additional_methods = True
            else:
                print("💡 Хорошее покрытие, но попробуем найти еще больше участников...")
                use_additional_methods = True
            
        except ChatAdminRequiredError:
            print("❌ ChatAdminRequiredError: Требуются права администратора")
            print("💡 Рекомендация: Для сбора полного списка участников этой группы/канала требуется быть админом")
            self.logger.warning("Агрессивный сбор заблокирован: требуются права администратора")
            use_additional_methods = True
            
        except ChannelPrivateError:
            print("❌ ChannelPrivateError: Канал/группа приватная или недоступна")
            print("💡 Рекомендация: Канал/группа закрыта для публичного доступа")
            self.logger.error("Доступ заблокирован: канал/группа приватная")
            return []
            
        except UserPrivacyRestrictedError:
            print("❌ UserPrivacyRestrictedError: Ограничения приватности пользователя")
            print("💡 Рекомендация: Настройки приватности не позволяют получить список участников")
            self.logger.warning("Сбор заблокирован: ограничения приватности")
            use_additional_methods = True
            
        except FloodWaitError as e:
            print(f"⏳ FloodWaitError: Необходимо подождать {e.seconds} секунд")
            print("💡 Рекомендация: Попробуйте позже или используйте другой аккаунт")
            self.logger.warning(f"FloodWait: ожидание {e.seconds} секунд")
            await asyncio.sleep(min(e.seconds, 300))  # Максимум 5 минут ожидания
            use_additional_methods = True
            
        except Exception as e:
            print(f"⚠️ Неожиданная ошибка агрессивного метода: {e}")
            self.logger.error(f"Ошибка агрессивного сбора: {e}")
            use_additional_methods = True
        
        # Анализируем результаты и предлагаем дополнительные методы
        if use_additional_methods:
            await self._offer_additional_methods(client, entity, all_participants, total_count)

        
        # Возвращаем все найденные участники
        final_participants = list(all_participants.values())
        
        if final_participants:
            print(f"🎯 ИТОГО найдено {len(final_participants)} уникальных участников")
            return final_participants
        else:
            print("❌ Все методы получения участников не дали результатов")
            print("💡 Возможные причины:")
            print("   • Канал/группа требует особых прав доступа")
            print("   • Канал/группа имеет ограничения на просмотр участников")
            print("   • Канал/группа приватная или неактивная")
            print("   • В группе/канале очень мало участников")
            
            return []
    
    async def _estimate_collection_potential(self, client: TelegramClient, entity, total_count: int):
        """Предварительная оценка потенциала сбора участников"""
        try:
            print("🔍 Выполняем тестовую оценку возможностей сбора...")
            
            # Тестовый запрос с малым лимитом для оценки
            test_participants = await client.get_participants(entity, limit=100)
            test_count = len(test_participants)
            
            if total_count and total_count > 0:
                estimated_coverage = min(100, (test_count / min(100, total_count)) * 100)
                
                if total_count > 10000:
                    print(f"⚠️ Большая группа ({total_count} участников)")
                    print("💡 Telegram не предоставляет полный список участников для больших групп без админ-прав")
                    print(f"📊 Прогноз сбора: ~{min(5000, total_count // 2)} участников из {total_count}")
                elif estimated_coverage < 50:
                    print(f"⚠️ Ограниченный доступ (тест: {test_count}/100)")
                    print("💡 Группа/канал имеет ограничения на просмотр участников")
                else:
                    print(f"✅ Хорошие перспективы сбора (тест: {test_count}/100)")
                    print(f"📊 Прогноз: возможно получить значительную часть от {total_count} участников")
            else:
                print("⚠️ Количество участников неизвестно, выполняем полный анализ...")
            
            # Небольшая задержка после тестового запроса
            await asyncio.sleep(0.2)
            
        except Exception as e:
            print(f"⚠️ Не удалось выполнить предварительную оценку: {e}")
            self.logger.debug(f"Ошибка предварительной оценки: {e}")
    
    async def _get_all_participants_paginated(self, client: TelegramClient, entity) -> List:
        """Получение всех участников с использованием пагинации"""
        all_participants = []
        offset = 0
        batch_count = 0
        
        print("📄 Используем пагинацию для максимального покрытия...")
        
        try:
            while True:
                batch_count += 1
                
                # Получаем батч участников
                try:
                    batch = await client.get_participants(
                        entity, 
                        aggressive=True,
                        limit=self.BATCH_SIZE
                    )
                    # Для пагинации используем срез
                    if offset > 0:
                        batch = batch[offset:offset + self.BATCH_SIZE] if offset < len(batch) else []
                except Exception as e:
                    print(f"⚠️ Ошибка получения батча: {e}")
                    batch = []
                
                if not batch:
                    print(f"✅ Пагинация завершена: обработано {batch_count} батчей")
                    break
                
                all_participants.extend(batch)
                offset += len(batch)
                
                print(f"📦 Батч {batch_count}: +{len(batch)} участников (всего: {len(all_participants)})")
                
                # Адаптивная задержка между батчами
                delay = self._get_adaptive_delay()
                await asyncio.sleep(delay)
                
                # Защита от бесконечного цикла
                if len(batch) < self.BATCH_SIZE:
                    print(f"✅ Получен неполный батч, завершаем пагинацию")
                    break
                
                # Ограничение для очень больших групп
                if len(all_participants) >= 50000:
                    print(f"⚠️ Достигнут лимит 50k участников, завершаем сбор")
                    break
            
            print(f"📊 Пагинация: собрано {len(all_participants)} участников за {batch_count} батчей")
            return all_participants
            
        except Exception as e:
            print(f"⚠️ Ошибка пагинации: {e}")
            self.logger.error(f"Ошибка пагинации: {e}")
            return all_participants  # Возвращаем что успели собрать
    
    async def _offer_additional_methods(self, client, entity, all_participants: dict, total_count: int):
        """Предлагает пользователю дополнительные методы сбора"""
        current_count = len(all_participants)
        
        print(f"\n📊 ПРОМЕЖУТОЧНЫЕ РЕЗУЛЬТАТЫ:")
        print(f"✅ Собрано участников: {current_count}")
        if total_count and total_count > 0:
            coverage = (current_count / total_count) * 100
            print(f"📈 Покрытие: {coverage:.1f}% от заявленного")
        
        print(f"\n💡 ДОСТУПНЫЕ ДОПОЛНИТЕЛЬНЫЕ МЕТОДЫ:")
        print("1. 📜 Анализ истории сообщений (может найти +100-1000 участников)")
        print("2. 🔍 Поиск по паттернам (может найти +50-500 участников)")
        print("3. 🚀 Использовать оба метода")
        print("4. ⏭️ Пропустить и завершить сбор")
        
        while True:
            try:
                choice = await self._async_input("\nВаш выбор (1/2/3/4): ")
                choice = choice.strip()
                
                if choice == '1':
                    await self._run_message_analysis(client, entity, all_participants)
                    break
                elif choice == '2':
                    await self._run_pattern_search(client, entity, all_participants)
                    break
                elif choice == '3':
                    await self._run_message_analysis(client, entity, all_participants)
                    await self._run_pattern_search(client, entity, all_participants)
                    break
                elif choice == '4':
                    print("⏭️ Завершаем сбор с текущими результатами")
                    break
                else:
                    print("❌ Неверный выбор. Введите 1, 2, 3 или 4")
                    
            except KeyboardInterrupt:
                print("\n❌ Операция прервана пользователем")
                break
    
    async def _run_message_analysis(self, client, entity, all_participants: dict):
        """Запускает анализ истории сообщений с отчетом"""
        print(f"\n🔄 АНАЛИЗ ИСТОРИИ СООБЩЕНИЙ")
        print("-" * 40)
        
        initial_count = len(all_participants)
        
        try:
            message_participants = await self._get_participants_from_messages(client, entity)
            
            added_count = 0
            for participant in message_participants:
                if participant.id not in all_participants:
                    all_participants[participant.id] = participant
                    added_count += 1
            
            print(f"📊 РЕЗУЛЬТАТЫ АНАЛИЗА СООБЩЕНИЙ:")
            print(f"   ➕ Новых участников: {added_count}")
            print(f"   📈 Было: {initial_count} → Стало: {len(all_participants)}")
            
            if added_count > 0:
                print(f"✅ Анализ сообщений добавил {added_count} участников!")
            else:
                print("⚠️ Анализ сообщений не дал новых участников")
                
        except Exception as e:
            print(f"❌ Ошибка анализа сообщений: {e}")
    
    async def _run_pattern_search(self, client, entity, all_participants: dict):
        """Запускает поиск по паттернам с выбором интенсивности"""
        print(f"\n🔍 ПОИСК ПО ПАТТЕРНАМ")
        print("-" * 30)
        
        print("Выберите интенсивность поиска:")
        print("1. 🟢 Быстрый (основные паттерны, ~50 запросов)")
        print("2. 🟡 Средний (расширенные паттерны, ~150 запросов)")
        print("3. 🔴 Полный (все паттерны, ~350 запросов)")
        
        while True:
            try:
                intensity = await self._async_input("Выберите интенсивность (1/2/3): ")
                intensity = intensity.strip()
                
                if intensity in ['1', '2', '3']:
                    break
                else:
                    print("❌ Введите 1, 2 или 3")
                    
            except KeyboardInterrupt:
                print("❌ Операция прервана")
                return
        
        initial_count = len(all_participants)
        
        try:
            # Генерируем паттерны в зависимости от выбранной интенсивности
            if intensity == '1':
                patterns = self._generate_basic_patterns()
                print(f"🟢 Быстрый поиск: {len(patterns)} паттернов")
            elif intensity == '2':
                patterns = self._generate_medium_patterns()
                print(f"🟡 Средний поиск: {len(patterns)} паттернов")
            else:
                patterns = self._generate_comprehensive_search_patterns()
                print(f"🔴 Полный поиск: {len(patterns)} паттернов")
            
            active_participants = await self._search_by_patterns(client, entity, patterns)
            
            added_count = 0
            for participant in active_participants:
                if participant.id not in all_participants:
                    all_participants[participant.id] = participant
                    added_count += 1
            
            print(f"\n📊 РЕЗУЛЬТАТЫ ПОИСКА ПО ПАТТЕРНАМ:")
            print(f"   🔍 Проверено паттернов: {len(patterns)}")
            print(f"   ➕ Новых участников: {added_count}")
            print(f"   📈 Было: {initial_count} → Стало: {len(all_participants)}")
            
            if added_count > 0:
                print(f"✅ Поиск по паттернам добавил {added_count} участников!")
            else:
                print("⚠️ Поиск по паттернам не дал новых участников")
                
        except Exception as e:
            print(f"❌ Ошибка поиска по паттернам: {e}")
    
    def _generate_basic_patterns(self) -> List[str]:
        """Генерирует базовые паттерны для быстрого поиска"""
        patterns = []
        
        # Основные гласные
        patterns.extend(['a', 'e', 'i', 'o', 'u', 'а', 'е', 'и', 'о', 'у'])
        
        # Популярные согласные
        patterns.extend(['n', 'r', 's', 't', 'l', 'м', 'н', 'р', 'с', 'т'])
        
        # Цифры
        patterns.extend(['1', '2', '3', '0'])
        
        # Популярные комбинации
        patterns.extend(['an', 'ar', 'er', 'in', 'on', 'ан', 'ар', 'ер', 'ин', 'он'])
        
        return patterns
    
    def _generate_medium_patterns(self) -> List[str]:
        """Генерирует средний набор паттернов"""
        patterns = self._generate_basic_patterns()
        
        # Добавляем все буквы кириллицы
        cyrillic = 'абвгдежзийклмнопрстуфхцчшщъыьэюя'
        patterns.extend(list(cyrillic))
        
        # Добавляем все буквы латиницы
        latin = 'abcdefghijklmnopqrstuvwxyz'
        patterns.extend(list(latin))
        
        # Популярные двухбуквенные комбинации
        popular_combinations = [
            'aa', 'ab', 'ac', 'ad', 'al', 'an', 'ar', 'as', 'at',
            'ba', 'be', 'bi', 'bo', 'ca', 'ch', 'co', 'da', 'de',
            'ав', 'ад', 'ак', 'ал', 'ан', 'ар', 'ас', 'ба', 'бе',
            'ва', 'ве', 'ви', 'во', 'га', 'ге', 'ги', 'го', 'да'
        ]
        patterns.extend(popular_combinations)
        
        return patterns
    
    async def _search_by_patterns(self, client, entity, patterns: List[str]) -> List:
        """Выполняет поиск по заданным паттернам с прогрессом"""
        participants_dict = {}
        successful_searches = 0
        total_patterns = len(patterns)
        
        print(f"🔍 Начинаем поиск по {total_patterns} паттернам...")
        
        for i, pattern in enumerate(patterns, 1):
            try:
                # Показываем прогресс каждые 10 паттернов
                if i % 10 == 0 or i == total_patterns:
                    progress = (i / total_patterns) * 100
                    print(f"📊 Прогресс: {i}/{total_patterns} ({progress:.1f}%) - найдено: {len(participants_dict)}")
                
                search_results = await client.get_participants(
                    entity, 
                    search=pattern, 
                    limit=100
                )
                
                new_users = 0
                for user in search_results:
                    if user.id not in participants_dict:
                        if not getattr(user, 'bot', False) and not getattr(user, 'deleted', False):
                            participants_dict[user.id] = user
                            new_users += 1
                
                if new_users > 0:
                    successful_searches += 1
                
                # Адаптивная задержка
                delay = self._get_adaptive_delay()
                await asyncio.sleep(delay)
                
                # Если нашли много участников, можем остановиться
                if len(participants_dict) >= 1000:
                    print("🎯 Найдено достаточно участников, завершаем поиск")
                    break
                
            except Exception as e:
                self.logger.debug(f"Поиск по паттерну '{pattern}' не удался: {e}")
                continue
        
        participants = list(participants_dict.values())
        print(f"🎯 Поиск завершен: найдено {len(participants)} участников через {successful_searches} успешных поисков")
        
        return participants
    
    def _generate_comprehensive_search_patterns(self) -> List[str]:
        """Генерирует исчерпывающий список паттернов для поиска"""
        patterns = []
        
        # Все буквы кириллицы
        cyrillic = 'абвгдежзийклмнопрстуфхцчшщъыьэюя'
        patterns.extend(list(cyrillic))
        
        # Все буквы латиницы
        latin = 'abcdefghijklmnopqrstuvwxyz'
        patterns.extend(list(latin))
        
        # Цифры
        digits = '0123456789'
        patterns.extend(list(digits))
        
        # Популярные двухбуквенные комбинации
        popular_combinations = [
            # Латинские
            'aa', 'ab', 'ac', 'ad', 'ae', 'al', 'an', 'ar', 'as', 'at',
            'ba', 'be', 'bi', 'bo', 'br', 'ca', 'ch', 'co', 'da', 'de',
            'di', 'do', 'el', 'en', 'er', 'es', 'ex', 'fa', 'fi', 'fo',
            'ga', 'ge', 'gi', 'go', 'ha', 'he', 'hi', 'ho', 'in', 'is',
            'it', 'ja', 'jo', 'ka', 'ke', 'ki', 'ko', 'la', 'le', 'li',
            'lo', 'ma', 'me', 'mi', 'mo', 'na', 'ne', 'ni', 'no', 'ol',
            'on', 'or', 'pa', 'pe', 'pi', 'po', 'ra', 're', 'ri', 'ro',
            'sa', 'se', 'si', 'so', 'ta', 'te', 'ti', 'to', 'va', 've',
            'vi', 'vo', 'wa', 'we', 'wi', 'wo', 'ya', 'ye', 'yo', 'za',
            
            # Кириллические
            'ав', 'ад', 'ак', 'ал', 'ан', 'ар', 'ас', 'ба', 'бе', 'би',
            'бо', 'бр', 'ва', 'ве', 'ви', 'во', 'вр', 'га', 'ге', 'ги',
            'го', 'гр', 'да', 'де', 'ди', 'до', 'др', 'ев', 'ег', 'ек',
            'ел', 'ем', 'ен', 'ер', 'ес', 'же', 'за', 'зе', 'зи', 'зо',
            'ив', 'иг', 'ик', 'ил', 'им', 'ин', 'ир', 'ис', 'ка', 'ке',
            'ки', 'ко', 'кр', 'ла', 'ле', 'ли', 'ло', 'лю', 'ма', 'ме',
            'ми', 'мо', 'му', 'на', 'не', 'ни', 'но', 'ну', 'об', 'од',
            'ок', 'ол', 'он', 'оп', 'ор', 'ос', 'от', 'па', 'пе', 'пи',
            'по', 'пр', 'ра', 'ре', 'ри', 'ро', 'ру', 'са', 'се', 'си',
            'со', 'ст', 'та', 'те', 'ти', 'то', 'тр', 'уб', 'уг', 'ук',
            'ул', 'ум', 'ун', 'ур', 'ус', 'ут', 'фа', 'фе', 'фи', 'фо',
            'ха', 'хе', 'хи', 'хо', 'це', 'ци', 'ча', 'че', 'чи', 'чо',
            'ша', 'ше', 'ши', 'шо', 'щи', 'эк', 'эл', 'эм', 'эн', 'эр',
            'эс', 'эт', 'юг', 'юл', 'юр', 'яг', 'як', 'ял', 'ян', 'яр'
        ]
        patterns.extend(popular_combinations)
        
        # Числовые комбинации
        number_combinations = [
            '00', '01', '02', '03', '04', '05', '06', '07', '08', '09',
            '10', '11', '12', '13', '14', '15', '16', '17', '18', '19',
            '20', '21', '22', '23', '24', '25', '30', '33', '40', '44',
            '50', '55', '60', '66', '70', '77', '80', '88', '90', '99'
        ]
        patterns.extend(number_combinations)
        
        # Специальные символы
        special_chars = ['_', '-', '.', 'x', 'z', 'q']
        patterns.extend(special_chars)
        
        print(f"🔍 Сгенерировано {len(patterns)} паттернов для поиска")
        return patterns
    
    async def _get_participants_from_messages(self, client: TelegramClient, entity) -> List:
        """Получение участников через анализ истории сообщений"""
        try:
            participants_dict = {}
            message_count = 0
            max_messages = 5000  # Увеличиваем лимит сообщений
            
            print("📜 Анализируем историю сообщений для поиска участников...")
            
            # Получаем сообщения за разные периоды для большего охвата
            try:
                async for message in client.iter_messages(entity, limit=max_messages):
                    message_count += 1
                    
                    if message.sender:
                        user_id = message.sender.id
                        if user_id not in participants_dict:
                            # Проверяем что это не бот и не удаленный аккаунт
                            if not getattr(message.sender, 'bot', False) and not getattr(message.sender, 'deleted', False):
                                participants_dict[user_id] = message.sender
                    
                    # Также проверяем упоминания в сообщениях
                    if hasattr(message, 'entities') and message.entities:
                        for entity_mention in message.entities:
                            if hasattr(entity_mention, 'user_id') and entity_mention.user_id:
                                try:
                                    mentioned_user = await client.get_entity(entity_mention.user_id)
                                    if mentioned_user.id not in participants_dict:
                                        if not getattr(mentioned_user, 'bot', False) and not getattr(mentioned_user, 'deleted', False):
                                            participants_dict[mentioned_user.id] = mentioned_user
                                except:
                                    pass  # Игнорируем ошибки получения упомянутых пользователей
                    
                    # Показываем прогресс каждые 200 сообщений
                    if message_count % 200 == 0:
                        print(f"📊 Проанализировано сообщений: {message_count}, найдено участников: {len(participants_dict)}")
                    
                    # Ограничиваем количество для избежания долгого ожидания
                    if len(participants_dict) >= 1000:
                        print("🎯 Достигнут лимит участников (1000), завершаем анализ")
                        break
                    
                    # Адаптивная задержка для избежания FloodWaitError (каждые 50 сообщений)
                    if message_count % 50 == 0:
                        delay = self._get_adaptive_delay()
                        await asyncio.sleep(delay)
                
            except Exception as e:
                print(f"⚠️ Ошибка при анализе сообщений: {e}")
            
            participants = list(participants_dict.values())
            print(f"📊 Найдено {len(participants)} уникальных участников из {message_count} сообщений")
            
            return participants
            
        except Exception as e:
            self.logger.error(f"Ошибка получения участников через сообщения: {e}")
            return []
    
    async def _get_active_participants(self, client: TelegramClient, entity) -> List:
        """Получение активных участников через поиск (устаревший метод, используется для совместимости)"""
        # Используем средний набор паттернов для совместимости
        patterns = self._generate_medium_patterns()
        return await self._search_by_patterns(client, entity, patterns)
    
    async def _analyze_collection_results(self, collected_count: int, declared_count: int, entity_title: str):
        """Анализ и отчетность о результатах сбора участников"""
        try:
            print(f"\n📊 АНАЛИЗ РЕЗУЛЬТАТОВ СБОРА")
            print("=" * 50)
            
            # Базовая статистика
            print(f"📺 Группа/канал: {entity_title}")
            print(f"👥 Заявлено участников: {declared_count if declared_count else 'Неизвестно'}")
            print(f"✅ Собрано участников: {collected_count}")
            
            # Расчет покрытия
            if declared_count and declared_count > 0:
                coverage_percent = (collected_count / declared_count) * 100
                print(f"📈 Покрытие: {coverage_percent:.1f}%")
                
                # Анализ качества покрытия
                if coverage_percent >= 90:
                    print("🎉 Отличное покрытие! Получена практически полная база участников")
                elif coverage_percent >= 70:
                    print("✅ Хорошее покрытие! Получена значительная часть участников")
                elif coverage_percent >= 50:
                    print("⚠️ Среднее покрытие. Telegram ограничивает доступ к части участников")
                elif coverage_percent >= 25:
                    print("⚠️ Низкое покрытие. Значительные ограничения доступа")
                else:
                    print("❌ Очень низкое покрытие. Серьезные ограничения Telegram")
                
                # Объяснение ограничений
                if coverage_percent < 80:
                    print("\n💡 ПОЧЕМУ НЕ ВСЕ УЧАСТНИКИ ДОСТУПНЫ:")
                    print("   • Telegram ограничивает доступ к спискам участников для борьбы со спамом")
                    print("   • Большие группы (>10к) никогда не выдают полный список без админ-прав")
                    print("   • Некоторые пользователи скрыты настройками приватности")
                    print("   • Технические ограничения API мессенджера")
                    
                    if declared_count > 10000:
                        print("   • Для групп >10к участников это нормальное поведение Telegram")
            
            # Рекомендации по улучшению
            if collected_count < 100:
                print(f"\n💡 РЕКОМЕНДАЦИИ ДЛЯ УЛУЧШЕНИЯ РЕЗУЛЬТАТОВ:")
                print("   • Попробуйте другой аккаунт с лучшей репутацией")
                print("   • Убедитесь что группа/канал действительно публичные")
                print("   • Проверьте, не заблокирован ли ваш аккаунт в этой группе")
                print("   • Для полного доступа требуются права администратора")
            
            # Логирование для анализа
            self.logger.info(f"Результат сбора: {collected_count}/{declared_count} участников ({coverage_percent:.1f}% покрытие)" if declared_count else f"Результат сбора: {collected_count} участников")
            
            print("=" * 50)
            
        except Exception as e:
            self.logger.error(f"Ошибка анализа результатов: {e}")
            print(f"⚠️ Не удалось выполнить анализ результатов: {e}")
    
    async def _suggest_alternatives(self, entity, entity_title: str):
        """Предложение альтернативных действий при неудачном сборе"""
        try:
            print(f"\n💡 РЕКОМЕНДАЦИИ ДЛЯ '{entity_title}':")
            print("=" * 60)
            
            # Анализ типа сущности
            is_channel = getattr(entity, 'broadcast', False)
            is_megagroup = getattr(entity, 'megagroup', False)
            participants_count = getattr(entity, 'participants_count', 0)
            
            if is_channel:
                print("📺 Это канал. Каналы часто имеют ограничения на просмотр подписчиков")
                print("   • Попробуйте найти связанную группу обсуждений")
                print("   • Обратитесь к администратору канала за списком")
            elif is_megagroup:
                print("👥 Это супергруппа. Для больших групп Telegram ограничивает доступ")
                print("   • Для полного списка нужны права администратора")
                print("   • Попробуйте использовать другой аккаунт")
            else:
                print("💬 Это обычная группа")
                print("   • Убедитесь что группа публичная")
                print("   • Проверьте настройки приватности группы")
            
            if participants_count and participants_count > 10000:
                print(f"⚠️ Большая группа ({participants_count} участников)")
                print("   • Telegram принципиально не выдает полные списки больших групп")
                print("   • Это защита от спама и злоупотреблений")
                print("   • Максимум можно получить ~5000-7000 участников")
            
            print(f"\n🔄 АЛЬТЕРНАТИВНЫЕ ДЕЙСТВИЯ:")
            print("   1. Попробовать другой аккаунт с лучшей репутацией")
            print("   2. Обратиться к администратору за правами")
            print("   3. Найти похожую, но более открытую группу")
            print("   4. Использовать частичные данные для тестирования")
            print("   5. Попробовать позже (ограничения могут быть временными)")
            
            print("=" * 60)
            
        except Exception as e:
            self.logger.error(f"Ошибка генерации рекомендаций: {e}")
            print("💡 Попробуйте другую группу/канал или обратитесь к администратору")
    
    async def _save_to_json(self, output_file: str = "data/messages_data.json") -> bool:
        """Сохранить собранные данные в JSON с объединением существующих данных"""
        try:
            if not self.users_data:
                print("❌ Нет данных для сохранения")
                return False
            
            # Создаем папку если её нет
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Проверяем существует ли файл и загружаем существующие данные
            existing_recipients = []
            existing_user_ids = set()
            
            if output_path.exists():
                try:
                    print(f"📁 Найден существующий файл {output_file}")
                    existing_data = await self._load_existing_data(output_file)
                    
                    if existing_data and 'recipients' in existing_data:
                        existing_recipients = existing_data['recipients']
                        existing_user_ids = {user.get('user_id') for user in existing_recipients if user.get('user_id')}
                        print(f"📊 Загружено {len(existing_recipients)} существующих пользователей")
                    else:
                        print("⚠️ Существующий файл пуст или имеет неверный формат, создаем новый")
                        
                except Exception as e:
                    self.logger.warning(f"Ошибка загрузки существующих данных: {e}")
                    print(f"⚠️ Не удалось загрузить существующие данные: {e}")
                    print("📝 Создаем новый файл")
            
            # Объединяем данные, исключая дубликаты по user_id
            combined_recipients = existing_recipients.copy()
            new_users_added = 0
            duplicates_skipped = 0
            
            for new_user in self.users_data:
                user_id = new_user.get('user_id')
                if user_id and user_id not in existing_user_ids:
                    combined_recipients.append(new_user)
                    existing_user_ids.add(user_id)
                    new_users_added += 1
                else:
                    duplicates_skipped += 1
            
            # Создаем финальную структуру данных
            final_data = {
                "message": "Временное сообщение - будет заменено при рассылке",
                "recipients": combined_recipients,
                "metadata": {
                    "total_users": len(combined_recipients),
                    "users_with_username": len([u for u in combined_recipients if u.get('username')]),
                    "users_with_phone": len([u for u in combined_recipients if u.get('phone')]),
                    "users_with_display_name": len([u for u in combined_recipients if u.get('display_name')]),
                    "collection_timestamp": time.time(),
                    "collector_version": "2.0",
                    "last_update": {
                        "new_users_added": new_users_added,
                        "duplicates_skipped": duplicates_skipped,
                        "existing_users": len(existing_recipients)
                    }
                }
            }
            
            # Сохраняем объединенные данные
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(final_data, f, ensure_ascii=False, indent=2)
            
            # Выводим статистику
            print(f"\n💾 Данные сохранены в {output_file}")
            print(f"📊 СТАТИСТИКА ОБЪЕДИНЕНИЯ:")
            print(f"   📈 Новых пользователей добавлено: {new_users_added}")
            if duplicates_skipped > 0:
                print(f"   🔄 Дубликатов пропущено: {duplicates_skipped}")
            if existing_recipients:
                print(f"   📋 Существующих пользователей: {len(existing_recipients)}")
            print(f"   📊 Всего пользователей: {final_data['metadata']['total_users']}")
            print(f"   • С username: {final_data['metadata']['users_with_username']}")
            print(f"   • С именем: {final_data['metadata']['users_with_display_name']}")
            print(f"   • С телефоном: {final_data['metadata']['users_with_phone']}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка сохранения в JSON: {e}", exc_info=True)
            print(f"❌ Ошибка сохранения: {e}")
            return False
    
    async def _load_existing_data(self, output_file: str) -> Optional[Dict]:
        """Загрузить существующие данные из JSON файла"""
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Проверяем базовую структуру
            if not isinstance(data, dict):
                self.logger.warning("Файл не содержит словарь")
                return None
            
            # Проверяем наличие ключа recipients
            if 'recipients' not in data:
                self.logger.warning("Файл не содержит ключ 'recipients'")
                return None
            
            # Проверяем что recipients это список
            if not isinstance(data['recipients'], list):
                self.logger.warning("'recipients' не является списком")
                return None
            
            self.logger.info(f"Успешно загружены существующие данные: {len(data['recipients'])} пользователей")
            return data
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Ошибка парсинга JSON: {e}")
            return None
        except FileNotFoundError:
            self.logger.info("Файл не найден, будет создан новый")
            return None
        except Exception as e:
            self.logger.error(f"Неожиданная ошибка при загрузке данных: {e}")
            return None
    
    @staticmethod
    def _extract_channel_username(channel_input: str) -> str:
        """Извлечь username канала из различных форматов ввода"""
        channel_input = channel_input.strip()
        
        # Если это ссылка t.me
        if 't.me/' in channel_input:
            username = channel_input.split('t.me/')[-1]
            username = username.split('?')[0]
            username = username.rstrip('/')
            return username
        
        # Если это ссылка telegram.me
        if 'telegram.me/' in channel_input:
            username = channel_input.split('telegram.me/')[-1]
            username = username.split('?')[0]
            username = username.rstrip('/')
            return username
        
        # Если начинается с @, убираем его
        if channel_input.startswith('@'):
            return channel_input[1:]
        
        return channel_input
    
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
    
    @staticmethod
    def validate_channel_input(channel_input: str) -> bool:
        """Проверить корректность ввода канала"""
        if not channel_input or not channel_input.strip():
            return False
        
        channel_input = channel_input.strip()
        
        valid_patterns = [
            't.me/' in channel_input,
            'telegram.me/' in channel_input,
            channel_input.startswith('@'),
            channel_input.replace('_', '').replace('-', '').isalnum()
        ]
        
        return any(valid_patterns)
    
    def set_collection_settings(self, delay: float = 0.1, max_users: int = 10000):
        """Настроить параметры сбора"""
        self.DELAY_BETWEEN_USERS = delay
        self.MAX_USERS_PER_CHANNEL = max_users
        self.logger.info(f"⚙️ Настройки сбора: задержка={delay}с, макс_пользователей={max_users}")
    
    async def collect_members_by_input_async(self, user_input: str) -> bool:
        """Асинхронная версия collect_members_by_input для использования в main.py"""
        # Определяем тип ввода
        input_type = self._detect_input_type(user_input)
        
        print(f"🔍 Обнаружен тип ввода: {input_type}")
        
        if input_type == "username_or_link":
            # Если это username или ссылка - сразу парсим
            return await self._collect_by_username_or_link(user_input)
        else:
            # Если это название - ищем по display name
            return await self.collect_members_by_display_name(user_input)
    
    def get_stats(self) -> Dict:
        """Получить статистику последнего сбора"""
        return {
            'total_users': len(self.users_data),
            'users_with_username': len([u for u in self.users_data if u.get('username')]),
            'users_with_phone': len([u for u in self.users_data if u.get('phone')]),
            'users_with_display_name': len([u for u in self.users_data if u.get('display_name')])
        }
    
    async def get_file_stats(self, output_file: str = "data/messages_data.json") -> Optional[Dict]:
        """Получить статистику из сохраненного файла"""
        try:
            data = await self._load_existing_data(output_file)
            if not data or 'recipients' not in data:
                return None
            
            recipients = data['recipients']
            return {
                'total_users': len(recipients),
                'users_with_username': len([u for u in recipients if u.get('username')]),
                'users_with_phone': len([u for u in recipients if u.get('phone')]),
                'users_with_display_name': len([u for u in recipients if u.get('display_name')]),
                'metadata': data.get('metadata', {})
            }
        except Exception as e:
            self.logger.error(f"Ошибка получения статистики файла: {e}")
            return None