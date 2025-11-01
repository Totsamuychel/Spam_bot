#!/usr/bin/env python3
"""
Скрипт для проверки User ID в Telegram
Позволяет получить ID пользователя по username или номеру телефона
"""

import asyncio
import json
import os
import sys
from telethon import TelegramClient
from telethon.errors import (
    UsernameNotOccupiedError, 
    PhoneNumberInvalidError,
    FloodWaitError,
    AuthKeyUnregisteredError,
    SessionPasswordNeededError
)

class UserIDChecker:
    """Класс для проверки и получения User ID"""
    
    def __init__(self):
        self.client = None
        self.api_id = None
        self.api_hash = None
        
    def load_config(self):
        """Загрузка конфигурации API"""
        try:
            if os.path.exists('config.json'):
                with open('config.json', 'r') as f:
                    config = json.load(f)
                    self.api_id = config.get('api_id')
                    self.api_hash = config.get('api_hash')
            
            if not self.api_id or not self.api_hash:
                print("❌ API ID и API Hash не настроены!")
                print("Создайте файл config.json с вашими данными:")
                print('{"api_id": 12345, "api_hash": "your_api_hash"}')
                return False
                
            return True
            
        except Exception as e:
            print(f"❌ Ошибка загрузки конфигурации: {e}")
            return False
    
    async def connect_client(self, session_name="user_id_checker"):
        """Подключение к Telegram"""
        try:
            self.client = TelegramClient(session_name, self.api_id, self.api_hash)
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                print("📱 Требуется авторизация...")
                phone = input("Введите номер телефона: ")
                await self.client.send_code_request(phone)
                code = input("Введите код из SMS: ")
                
                try:
                    await self.client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    print("🔐 Требуется пароль двухфакторной аутентификации")
                    password = input("Введите пароль 2FA: ")
                    await self.client.sign_in(password=password)
                except Exception as e:
                    if "Two-steps verification" in str(e) or "password is required" in str(e):
                        print("🔐 Требуется пароль двухфакторной аутентификации")
                        password = input("Введите пароль 2FA: ")
                        await self.client.sign_in(password=password)
                    else:
                        raise e
                
            print("✅ Успешно подключен к Telegram")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка подключения: {e}")
            return False
    
    async def get_user_info(self, identifier):
        """Получение информации о пользователе"""
        try:
            # Определяем тип идентификатора
            if identifier.startswith('@'):
                # Username
                username = identifier[1:]  # Убираем @
                entity = await self.client.get_entity(username)
            elif identifier.startswith('+') or identifier.isdigit():
                # Номер телефона или ID
                if identifier.startswith('+'):
                    entity = await self.client.get_entity(identifier)
                else:
                    # Пробуем как ID
                    entity = await self.client.get_entity(int(identifier))
            else:
                # Пробуем как username без @
                entity = await self.client.get_entity(identifier)
            
            # Собираем информацию
            user_info = {
                'id': entity.id,
                'username': getattr(entity, 'username', None),
                'first_name': getattr(entity, 'first_name', None),
                'last_name': getattr(entity, 'last_name', None),
                'phone': getattr(entity, 'phone', None),
                'is_bot': getattr(entity, 'bot', False),
                'is_verified': getattr(entity, 'verified', False),
                'is_premium': getattr(entity, 'premium', False),
                'is_scam': getattr(entity, 'scam', False),
                'is_fake': getattr(entity, 'fake', False)
            }
            
            return user_info
            
        except UsernameNotOccupiedError:
            print(f"❌ Пользователь {identifier} не найден")
            return None
        except PhoneNumberInvalidError:
            print(f"❌ Неверный номер телефона: {identifier}")
            return None
        except ValueError as e:
            if "No user has" in str(e):
                print(f"❌ Пользователь с ID {identifier} не найден")
            else:
                print(f"❌ Ошибка: {e}")
            return None
        except FloodWaitError as e:
            print(f"⏳ Превышен лимит запросов. Ждите {e.seconds} секунд")
            return None
        except Exception as e:
            print(f"❌ Неожиданная ошибка: {e}")
            return None
    
    def print_user_info(self, user_info):
        """Красивый вывод информации о пользователе"""
        if not user_info:
            return
            
        print("\n" + "="*50)
        print("📋 ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ")
        print("="*50)
        print(f"🆔 User ID: {user_info['id']}")
        
        if user_info['username']:
            print(f"👤 Username: @{user_info['username']}")
        
        name_parts = []
        if user_info['first_name']:
            name_parts.append(user_info['first_name'])
        if user_info['last_name']:
            name_parts.append(user_info['last_name'])
        
        if name_parts:
            print(f"📝 Имя: {' '.join(name_parts)}")
        
        if user_info['phone']:
            print(f"📞 Телефон: +{user_info['phone']}")
        
        # Статусы
        statuses = []
        if user_info['is_bot']:
            statuses.append("🤖 Бот")
        if user_info['is_verified']:
            statuses.append("✅ Верифицирован")
        if user_info['is_premium']:
            statuses.append("⭐ Premium")
        if user_info['is_scam']:
            statuses.append("⚠️ Скам")
        if user_info['is_fake']:
            statuses.append("🚫 Фейк")
        
        if statuses:
            print(f"🏷️ Статус: {', '.join(statuses)}")
        
        print("="*50)
    
    async def batch_check(self, identifiers):
        """Массовая проверка пользователей"""
        results = []
        
        print(f"🔍 Проверяю {len(identifiers)} пользователей...")
        
        for i, identifier in enumerate(identifiers, 1):
            print(f"\n[{i}/{len(identifiers)}] Проверяю: {identifier}")
            
            user_info = await self.get_user_info(identifier.strip())
            if user_info:
                results.append(user_info)
                self.print_user_info(user_info)
            
            # Небольшая задержка между запросами
            if i < len(identifiers):
                await asyncio.sleep(1)
        
        return results
    
    async def save_results(self, results, filename="user_ids_results.json"):
        """Сохранение результатов в файл"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"\n💾 Результаты сохранены в {filename}")
        except Exception as e:
            print(f"❌ Ошибка сохранения: {e}")
    
    async def disconnect(self):
        """Отключение от Telegram"""
        if self.client:
            await self.client.disconnect()

async def main():
    """Главная функция"""
    checker = UserIDChecker()
    
    # Загружаем конфигурацию
    if not checker.load_config():
        return
    
    # Подключаемся к Telegram
    if not await checker.connect_client():
        return
    
    try:
        while True:
            print("\n" + "="*50)
            print("🔍 ПРОВЕРКА USER ID TELEGRAM")
            print("="*50)
            print("1. Проверить одного пользователя")
            print("2. Массовая проверка из файла")
            print("3. Массовая проверка (ввод вручную)")
            print("4. Выход")
            
            choice = input("\nВыберите действие: ").strip()
            
            if choice == '1':
                identifier = input("Введите username (@user), номер (+1234567890) или ID: ").strip()
                if identifier:
                    user_info = await checker.get_user_info(identifier)
                    checker.print_user_info(user_info)
            
            elif choice == '2':
                filename = input("Введите имя файла (по умолчанию users.txt): ").strip()
                if not filename:
                    filename = "users.txt"
                
                try:
                    with open(filename, 'r', encoding='utf-8') as f:
                        identifiers = [line.strip() for line in f if line.strip()]
                    
                    if identifiers:
                        results = await checker.batch_check(identifiers)
                        if results:
                            save_choice = input("\nСохранить результаты в файл? (y/n): ").strip().lower()
                            if save_choice == 'y':
                                await checker.save_results(results)
                    else:
                        print("❌ Файл пустой или не содержит пользователей")
                        
                except FileNotFoundError:
                    print(f"❌ Файл {filename} не найден")
                except Exception as e:
                    print(f"❌ Ошибка чтения файла: {e}")
            
            elif choice == '3':
                print("Введите пользователей (по одному в строке, пустая строка для завершения):")
                identifiers = []
                while True:
                    user_input = input("Пользователь: ").strip()
                    if not user_input:
                        break
                    identifiers.append(user_input)
                
                if identifiers:
                    results = await checker.batch_check(identifiers)
                    if results:
                        save_choice = input("\nСохранить результаты в файл? (y/n): ").strip().lower()
                        if save_choice == 'y':
                            await checker.save_results(results)
            
            elif choice == '4':
                break
            
            else:
                print("❌ Неверный выбор")
    
    except KeyboardInterrupt:
        print("\n\n👋 Прерывание пользователем")
    
    finally:
        await checker.disconnect()

if __name__ == "__main__":
    asyncio.run(main())