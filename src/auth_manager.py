#!/usr/bin/env python3
"""
Модуль для авторизации новых аккаунтов Telegram
УПРОЩЕННАЯ ВЕРСИЯ БЕЗ ПОВТОРНЫХ ОТПРАВОК КОДА
"""

import asyncio
import logging
import os
import time
from typing import Dict
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    FloodWaitError
)

class AuthManager:
    """Управление авторизацией аккаунтов"""
    
    def __init__(self, api_id: int, api_hash: str, sessions_dir: str = "sessions"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.sessions_dir = sessions_dir
        self.logger = logging.getLogger(__name__)
        
        # Создаем папку sessions если её нет
        if not os.path.exists(sessions_dir):
            os.makedirs(sessions_dir)
    
    async def add_new_account(self, account_name: str = None) -> bool:
        """Добавление нового аккаунта через авторизацию - УПРОЩЕННАЯ ВЕРСИЯ"""
        try:
            if not account_name:
                account_name = input("Введите имя для аккаунта (например: Аккаунт1: ").strip()
                if not account_name:
                    print("❌ Имя аккаунта не может быть пустым")
                    return False
            
            session_path = os.path.join(self.sessions_dir, account_name)
            
            # Проверяем что аккаунт не существует
            if os.path.exists(f"{session_path}.session"):
                overwrite = input(f"Аккаунт {account_name} уже существует. Перезаписать? (y/n): ").strip().lower()
                if overwrite != 'y':
                    return False
            
            print(f"\n🔐 Авторизация аккаунта: {account_name}")
            print("=" * 50)
            
            # Создаем клиент
            client = TelegramClient(session_path, self.api_id, self.api_hash)
            await client.connect()
            
            if await client.is_user_authorized():
                print("✅ Аккаунт уже авторизован")
                me = await client.get_me()
                account_info = f"{me.first_name} (@{me.username})" if me.username else me.first_name
                print(f"👤 Аккаунт: {account_info}")
                await client.disconnect()
                return True
            
            # Запрашиваем номер телефона
            phone = input("📱 Введите номер телефона (с кодом страны, например +380501234567): ").strip()
            if not phone:
                print("❌ Номер телефона не может быть пустым")
                await client.disconnect()
                return False
            
            # Отправляем код ОДИН РАЗ
            print("📤 Отправляем код авторизации...")
            try:
                await client.send_code_request(phone)
                print("✅ Код отправлен! Проверьте Telegram на всех устройствах")
                print("💡 Код приходит в приложение Telegram, НЕ в SMS")
            except PhoneNumberInvalidError:
                print("❌ Неверный номер телефона")
                print("💡 Формат должен быть: +код_страны номер (например: +380501234567)")
                await client.disconnect()
                return False
            except FloodWaitError as e:
                print(f"⏳ Превышен лимит запросов. Ждите {e.seconds} секунд ({e.seconds // 60} минут)")
                print("💡 Слишком много попыток авторизации. Попробуйте позже.")
                await client.disconnect()
                return False
            except Exception as e:
                print(f"❌ Ошибка отправки кода: {e}")
                await client.disconnect()
                return False
            
            # Запрашиваем код ОДИН РАЗ (без повторных отправок!)
            code = input("\n📨 Введите код из Telegram: ").strip()
            if not code:
                print("❌ Код не может быть пустым")
                await client.disconnect()
                return False
            
            # Авторизуемся
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                print("🔐 Требуется пароль двухфакторной аутентификации")
                password = input("Введите пароль 2FA: ").strip()
                if not password:
                    print("❌ Пароль не может быть пустым")
                    await client.disconnect()
                    return False
                await client.sign_in(password=password)
            except PhoneCodeInvalidError:
                print("❌ Неверный код из Telegram")
                print("💡 Если код не пришел, подождите 1-2 часа и попробуйте снова")
                await client.disconnect()
                return False
            except Exception as e:
                print(f"❌ Ошибка авторизации: {e}")
                await client.disconnect()
                return False
            
            # Получаем информацию об аккаунте
            me = await client.get_me()
            account_info = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            
            print("✅ Авторизация успешна!")
            print(f"👤 Аккаунт: {account_info}")
            print(f"🆔 User ID: {me.id}")
            print(f"📞 Телефон: +{me.phone}")
            
            await client.disconnect()
            
            self.logger.info(f"Добавлен новый аккаунт {account_name}: {account_info}")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка авторизации: {e}")
            self.logger.error(f"Ошибка авторизации аккаунта {account_name}: {e}")
            return False
    
    async def check_account_info(self, session_path: str, skip_test: bool = False, use_cache: bool = False) -> dict:
        """Получение информации об аккаунте"""
        try:
            if not os.path.exists(f"{session_path}.session"):
                return {'success': False, 'error': 'Файл сессии не найден'}
            
            client = TelegramClient(session_path, self.api_id, self.api_hash)
            
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await asyncio.wait_for(client.connect(), timeout=10.0)
                    break
                except (OSError, asyncio.TimeoutError) as e:
                    if attempt == max_retries - 1:
                        return {'success': False, 'error': f'Не удалось подключиться после {max_retries} попыток: {e}'}
                    await asyncio.sleep(2 ** attempt)
            
            if not await client.is_user_authorized():
                await client.disconnect()
                return {'success': False, 'error': 'Не авторизован'}
            
            try:
                me = await asyncio.wait_for(client.get_me(), timeout=5.0)
            except asyncio.TimeoutError:
                await client.disconnect()
                return {'success': False, 'error': 'Таймаут при получении информации об аккаунте'}
            
            can_send = True
            send_error = None
            
            if not skip_test:
                try:
                    test_msg = await asyncio.wait_for(
                        client.send_message('me', '🔍 Тест подключения'), 
                        timeout=10.0
                    )
                    await asyncio.wait_for(test_msg.delete(), timeout=5.0)
                except asyncio.TimeoutError:
                    can_send = False
                    send_error = "Таймаут при отправке тестового сообщения"
                except Exception as e:
                    can_send = False
                    send_error = str(e)
            
            info = {
                'success': True,
                'id': me.id,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'username': me.username,
                'phone': me.phone,
                'is_premium': getattr(me, 'premium', False),
                'is_verified': getattr(me, 'verified', False),
                'can_send_messages': can_send,
                'send_error': send_error
            }
            
            await client.disconnect()
            return info
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    async def list_all_accounts(self) -> list:
        """Список всех аккаунтов с информацией"""
        accounts = []
        
        if not os.path.exists(self.sessions_dir):
            return accounts
        
        session_files = [f for f in os.listdir(self.sessions_dir) if f.endswith('.session')]
        
        for session_file in session_files:
            account_name = session_file.replace('.session', '')
            session_path = os.path.join(self.sessions_dir, session_file).replace('.session', '')
            
            print(f"🔍 Проверяю аккаунт {account_name}...")
            info = await self.check_account_info(session_path, skip_test=True)
            
            account_data = {
                'name': account_name,
                'session_path': session_path,
                **info
            }
            
            accounts.append(account_data)
        
        return accounts
    
    def print_account_info(self, account_data: dict):
        """Красивый вывод информации об аккаунте"""
        print("\n" + "="*60)
        print(f"📱 АККАУНТ: {account_data['name']}")
        print("="*60)
        
        if not account_data['success']:
            print(f"❌ Ошибка: {account_data['error']}")
            return
        
        name_parts = []
        if account_data['first_name']:
            name_parts.append(account_data['first_name'])
        if account_data['last_name']:
            name_parts.append(account_data['last_name'])
        
        print(f"👤 Имя: {' '.join(name_parts)}")
        
        if account_data['username']:
            print(f"🔗 Username: @{account_data['username']}")
        
        print(f"🆔 User ID: {account_data['id']}")
        print(f"📞 Телефон: +{account_data['phone']}")
        
        statuses = []
        if account_data['is_premium']:
            statuses.append("⭐ Premium")
        if account_data['is_verified']:
            statuses.append("✅ Верифицирован")
        
        if statuses:
            print(f"🏷️ Статус: {', '.join(statuses)}")
        
        if account_data['can_send_messages']:
            print("✅ Может отправлять сообщения")
        else:
            print(f"❌ Не может отправлять сообщения: {account_data['send_error']}")
        
        print("="*60)
    
    async def interactive_account_management(self):
        """Интерактивное управление аккаунтами"""
        while True:
            print("\n" + "="*50)
            print("🔐 ДОБАВИТЬ/УБРАТЬ АККАУНТЫ")
            print("="*50)
            print("1. Новый аккаунт")
            print("2. Удалить аккаунт")
            print("3. Все аккаунты")
            print("4. Проверить конкретный аккаунт")
            print("0. В главное меню")
            
            choice = input("\nВыберите действие: ").strip()
            
            if choice == '1':
                await self.add_new_account()
            
            elif choice == '3':
                accounts = await self.list_all_accounts()
                if not accounts:
                    print("📭 Нет добавленных аккаунтов")
                else:
                    for account in accounts:
                        self.print_account_info(account)
            
            elif choice == '4':
                account_name = input("Введите имя аккаунта: ").strip()
                if account_name:
                    session_path = os.path.join(self.sessions_dir, account_name)
                    if os.path.exists(f"{session_path}.session"):
                        info = await self.check_account_info(session_path)
                        account_data = {'name': account_name, **info}
                        self.print_account_info(account_data)
                    else:
                        print(f"❌ Аккаунт {account_name} не найден")
            
            elif choice == '2':
                account_name = input("Введите имя аккаунта для удаления: ").strip()
                if account_name:
                    session_file = os.path.join(self.sessions_dir, f"{account_name}.session")
                    if os.path.exists(session_file):
                        confirm = input(f"Удалить аккаунт {account_name}? (y/n): ").strip().lower()
                        if confirm == 'y':
                            os.remove(session_file)
                            print(f"✅ Аккаунт {account_name} удален")
                    else:
                        print(f"❌ Аккаунт {account_name} не найден")
            
            elif choice == '0':
                break
            
            else:
                print("❌ Неверный выбор")
