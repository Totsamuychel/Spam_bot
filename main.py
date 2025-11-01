#!/usr/bin/env python3
"""
Telegram Multi-Account Message Sender
Основной файл запуска бота для безопасной рассылки сообщений
"""

import asyncio
import logging
import sys
import os
from typing import Dict, List
import json

# Добавляем src в путь для импортов
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.account_manager import AccountManager
from src.rate_limiter import RateLimiter
from src.message_queue import MessageQueue, MessageTask
from src.sender import MessageSender
from src.auth_manager import AuthManager

class TelegramBot:
    """Главный класс бота для управления рассылкой"""
    
    def __init__(self):
        self.setup_logging()
        
        self.account_manager = AccountManager()
        self.rate_limiter = RateLimiter()
        self.message_queue = MessageQueue()
        self.sender = MessageSender()
        self.auth_manager = None  # Инициализируется после загрузки конфига
        
        self.is_running = False
        self.stats = {
            'messages_sent': 0,
            'messages_failed': 0,
            'accounts_blocked': 0,
            'start_time': None
        }
        
        # Настройки API (нужно заполнить)
        self.api_id = None
        self.api_hash = None
        
    def setup_logging(self):
        """Настройка логирования"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('bot.log', encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def load_config(self):
        """Загрузка конфигурации"""
        try:
            if os.path.exists('config.json'):
                with open('config.json', 'r') as f:
                    config = json.load(f)
                    self.api_id = config.get('api_id')
                    self.api_hash = config.get('api_hash')
            
            if not self.api_id or not self.api_hash:
                self.logger.error("API ID и API Hash не настроены!")
                self.logger.info("Создайте файл config.json с вашими данными:")
                self.logger.info('{"api_id": 12345, "api_hash": "your_api_hash"}')
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка загрузки конфигурации: {e}")
            return False
    
    async def initialize(self):
        """Инициализация всех компонентов"""
        self.logger.info("Инициализация бота...")
        
        # Загружаем конфигурацию
        if not self.load_config():
            return False
        
        # Инициализируем менеджер авторизации
        self.auth_manager = AuthManager(self.api_id, self.api_hash)
        
        # Загружаем аккаунты
        if not self.account_manager.load_accounts():
            print("\n⚠️ Не найдено аккаунтов для рассылки!")
            print("Необходимо добавить хотя бы один аккаунт.")
            
            add_account = input("Хотите добавить аккаунт сейчас? (y/n): ").strip().lower()
            if add_account == 'y':
                success = await self.auth_manager.add_new_account()
                if success:
                    # Перезагружаем аккаунты
                    if not self.account_manager.load_accounts():
                        self.logger.error("Не удалось загрузить аккаунты после добавления")
                        return False
                else:
                    self.logger.error("Не удалось добавить аккаунт")
                    return False
            else:
                self.logger.error("Нет аккаунтов для работы")
                return False
        
        # Подключаем аккаунты
        connected_accounts = 0
        for account_name in self.account_manager.accounts.keys():
            if await self.account_manager.connect_account(account_name, self.api_id, self.api_hash):
                connected_accounts += 1
                # Получаем информацию об аккаунте
                account_data = self.account_manager.accounts[account_name]
                if account_data['client']:
                    try:
                        me = await account_data['client'].get_me()
                        account_info = f"{me.first_name} (@{me.username})" if me.username else me.first_name
                        self.logger.info(f"✅ Подключен аккаунт {account_name}: {account_info}")
                    except Exception as e:
                        self.logger.warning(f"Не удалось получить информацию об аккаунте {account_name}: {e}")
                        self.logger.info(f"✅ Подключен аккаунт {account_name}")
        
        if connected_accounts == 0:
            self.logger.error("Не удалось подключить ни одного аккаунта")
            return False
        
        self.logger.info(f"Всего подключено {connected_accounts} аккаунтов")
        
        # Загружаем данные сообщений
        if not self.message_queue.load_messages_data():
            self.logger.error("Не удалось загрузить данные сообщений")
            return False
        
        return True
    
    async def start_sending(self, max_messages: int = None):
        """Запуск процесса рассылки"""
        if self.is_running:
            self.logger.warning("Рассылка уже запущена")
            return
        
        self.is_running = True
        self.stats['start_time'] = asyncio.get_event_loop().time()
        
        try:
            # Получаем активные аккаунты
            active_accounts = [name for name, data in self.account_manager.accounts.items() 
                             if data['is_active'] and name not in self.account_manager.blocked_accounts]
            
            if not active_accounts:
                self.logger.error("Нет активных аккаунтов для рассылки")
                return
            
            # Создаем очередь сообщений
            tasks_created = self.message_queue.create_message_queue(active_accounts, max_messages)
            if tasks_created == 0:
                self.logger.error("Не удалось создать задачи для рассылки")
                return
            
            self.logger.info(f"Начинаем рассылку {tasks_created} сообщений через {len(active_accounts)} аккаунтов")
            
            # Основной цикл рассылки
            message_count = 0
            while self.is_running and not self.message_queue.message_queue.empty():
                await self.process_message_batch()
                message_count += 1
                
                # Очистка памяти каждые 100 сообщений
                if message_count % 100 == 0:
                    self.rate_limiter.cleanup_all_accounts()
                    self.logger.info(f"🧹 Очистка памяти после {message_count} сообщений")
                
                # Проверка здоровья подключений каждые 50 сообщений
                if message_count % 50 == 0:
                    try:
                        reconnected = await self.account_manager.auto_reconnect_failed(self.api_id, self.api_hash)
                        if reconnected > 0:
                            self.logger.info(f"🔄 Переподключено {reconnected} аккаунтов")
                    except Exception as e:
                        self.logger.warning(f"Ошибка при проверке здоровья подключений: {e}")
                
                # Небольшая пауза между батчами
                await asyncio.sleep(1)
            
            # Завершение рассылки
            await self.finish_sending()
            
        except Exception as e:
            self.logger.error(f"Ошибка в процессе рассылки: {e}")
        finally:
            self.is_running = False
    
    async def process_message_batch(self):
        """Обработка батча сообщений"""
        # Получаем активные аккаунты
        active_accounts = [name for name, data in self.account_manager.accounts.items() 
                          if data['is_active'] and name not in self.account_manager.blocked_accounts]
        
        if not active_accounts:
            self.logger.warning("Нет активных аккаунтов, останавливаем рассылку")
            self.is_running = False
            return
        
        # Обрабатываем задачи по одной
        task = self.message_queue.get_next_task()
        if task:
            await self.process_single_message(task)
        else:
            # Если задач нет, завершаем
            self.is_running = False
    
    async def process_single_message(self, task: MessageTask):
        """Обработка одного сообщения"""
        account_name = task.account_name
        
        try:
            self.logger.info(f"🔄 Обрабатываю задачу для аккаунта {account_name}")
            self.logger.info(f"📋 Получатель: ID={task.recipient_id}, Username={task.recipient_username}, Phone={task.recipient_phone}")
            
            # Проверяем лимиты
            can_send, wait_time = self.rate_limiter.can_send_message(account_name, task.is_new_chat)
            
            if not can_send:
                self.logger.info(f"⏳ Лимит для {account_name}, ждем {wait_time:.1f}с")
                await asyncio.sleep(wait_time)
                # Возвращаем задачу в очередь
                self.message_queue.message_queue.put(task)
                return
            
            # Получаем клиент аккаунта
            account_data = self.account_manager.accounts[account_name]
            client = account_data['client']
            
            if not client:
                self.logger.error(f"❌ Клиент для {account_name} не найден")
                self.message_queue.requeue_failed_task(task)
                self.stats['messages_failed'] += 1
                return
            
            self.logger.info(f"📤 Отправляю сообщение через {account_name}")
            
            # Отправляем сообщение
            result = await self.sender.send_message(client, task)
            
            self.logger.info(f"📊 Результат отправки: {result}")
            
            # Анализируем результат
            analysis = self.sender.analyze_send_result(result)
            
            if result['success']:
                # Успешная отправка
                self.rate_limiter.record_message_sent(account_name, task.is_new_chat)
                self.message_queue.mark_task_completed(task)
                self.stats['messages_sent'] += 1
                
                self.logger.info(f"✅ Сообщение успешно отправлено!")
                
                # Умная задержка
                await self.rate_limiter.smart_delay(account_name)
                
            else:
                # Обработка ошибки
                self.logger.warning(f"❌ Ошибка отправки: {result.get('error', 'Unknown')}")
                await self.handle_send_error(task, result, analysis)
            
        except Exception as e:
            self.logger.error(f"💥 Критическая ошибка обработки сообщения для {account_name}: {e}")
            self.message_queue.requeue_failed_task(task)
            self.stats['messages_failed'] += 1
    
    async def handle_send_error(self, task: MessageTask, result: Dict, analysis: Dict):
        """Обработка ошибок отправки"""
        account_name = task.account_name
        
        # Критические ошибки - блокируем аккаунт
        if result.get('should_block_account', False):
            self.account_manager.mark_account_blocked(account_name, result.get('error', 'unknown'))
            self.rate_limiter.record_account_blocked(account_name, result.get('error', 'unknown'))
            self.stats['accounts_blocked'] += 1
            
            # Перераспределяем задачи с заблокированного аккаунта
            active_accounts = [name for name, data in self.account_manager.accounts.items() 
                             if data['is_active'] and name not in self.account_manager.blocked_accounts]
            self.message_queue.redistribute_tasks(account_name, active_accounts)
        
        # Ошибки с ожиданием
        elif analysis['should_wait']:
            await asyncio.sleep(min(analysis['wait_time'], 300))  # Максимум 5 минут
            self.message_queue.requeue_failed_task(task)
        
        # Обычные ошибки - повторяем попытку
        elif result.get('should_retry', True):
            self.message_queue.requeue_failed_task(task)
        
        self.stats['messages_failed'] += 1
    
    async def finish_sending(self):
        """Завершение процесса рассылки"""
        self.logger.info("Рассылка завершена")
        
        # Сохраняем неудачные сообщения
        self.message_queue.save_failed_messages()
        
        # Выводим статистику
        await self.print_final_stats()
        
        # Отключаем аккаунты
        await self.account_manager.disconnect_all()
    
    async def print_final_stats(self):
        """Вывод финальной статистики"""
        queue_stats = self.message_queue.get_queue_stats()
        account_stats = self.account_manager.get_account_stats()
        
        elapsed_time = asyncio.get_event_loop().time() - self.stats['start_time']
        
        self.logger.info("=== СТАТИСТИКА РАССЫЛКИ ===")
        self.logger.info(f"Время выполнения: {elapsed_time:.1f} секунд")
        self.logger.info(f"Отправлено сообщений: {self.stats['messages_sent']}")
        self.logger.info(f"Неудачных отправок: {self.stats['messages_failed']}")
        self.logger.info(f"Заблокировано аккаунтов: {self.stats['accounts_blocked']}")
        self.logger.info(f"Процент успеха: {queue_stats['completion_rate']:.1f}%")
        self.logger.info(f"Активных аккаунтов: {account_stats['active_accounts']}")
    
    def stop_sending(self):
        """Остановка рассылки"""
        self.logger.info("Получен сигнал остановки рассылки")
        self.is_running = False

async def main():
    """Главная функция"""
    bot = TelegramBot()
    
    try:
        # Инициализация
        if not await bot.initialize():
            return
        
        # Интерактивное меню
        while True:
            print("\n=== TELEGRAM MULTI-ACCOUNT SENDER ===")
            print("1. Начать рассылку")
            print("2. Показать статистику аккаунтов")
            print("3. Показать лимиты аккаунтов")
            print("4. Тест подключения аккаунтов")
            print("5. 🔐 Управление аккаунтами (добавить/проверить)")
            print("6. 📋 Показать все аккаунты с подробной информацией")
            print("7. 🧹 Очистить память и сбросить лимиты")
            print("8. 🔄 Проверить и переподключить аккаунты")
            print("9. Выход")
            
            choice = input("Выберите действие: ").strip()
            
            if choice == '1':
                max_msg = input("Максимум сообщений (Enter для всех): ").strip()
                max_messages = int(max_msg) if max_msg.isdigit() else None
                await bot.start_sending(max_messages)
                
            elif choice == '2':
                stats = bot.account_manager.get_account_stats()
                print(json.dumps(stats, indent=2, ensure_ascii=False))
                
            elif choice == '3':
                print("\n📊 ЛИМИТЫ И ИСПОЛЬЗОВАНИЕ ПАМЯТИ")
                print("="*50)
                
                # Информация об использовании памяти
                memory_info = bot.rate_limiter.get_memory_usage_info()
                print(f"💾 Использование памяти:")
                print(f"   Отслеживается аккаунтов: {memory_info['accounts_tracked']}")
                print(f"   Записей сообщений: {memory_info['total_message_records']}")
                print(f"   Записей чатов: {memory_info['total_chat_records']}")
                print(f"   Примерное использование: {memory_info['memory_usage_estimate_mb']:.2f} MB")
                print(f"   Аккаунтов со штрафами: {memory_info['accounts_with_penalties']}")
                
                print(f"\n📋 Лимиты по аккаунтам:")
                for account_name in bot.account_manager.accounts.keys():
                    limits = bot.rate_limiter.get_account_limits_info(account_name)
                    print(f"   {account_name}: {limits}")
                    
            elif choice == '4':
                print("\n🔍 ТЕСТ ПОДКЛЮЧЕНИЯ АККАУНТОВ")
                print("="*50)
                
                if not bot.account_manager.accounts:
                    print("📭 Нет загруженных аккаунтов")
                else:
                    for account_name, data in bot.account_manager.accounts.items():
                        if data['client'] and data['is_active']:
                            result = await bot.sender.test_account_connection(data['client'], account_name)
                            if result['success']:
                                account_info = result['account_info']
                                username = f"@{account_info['username']}" if account_info['username'] else "Нет username"
                                print(f"✅ {account_name}: {account_info['first_name']} ({username}) - ID: {account_info['id']}")
                                print(f"   📞 Телефон: +{account_info['phone']}")
                            else:
                                print(f"❌ {account_name}: ERROR - {result.get('error', 'Unknown error')}")
                        else:
                            print(f"⚠️ {account_name}: Не подключен или неактивен")
                        
            elif choice == '5':
                await bot.auth_manager.interactive_account_management()
                # Перезагружаем аккаунты после изменений
                bot.account_manager.load_accounts()
                
            elif choice == '6':
                print("\n🔍 ПРОВЕРКА ВСЕХ АККАУНТОВ")
                print("="*60)
                accounts = await bot.auth_manager.list_all_accounts()
                if not accounts:
                    print("📭 Нет добавленных аккаунтов")
                else:
                    print(f"Найдено {len(accounts)} аккаунтов:")
                    for account in accounts:
                        bot.auth_manager.print_account_info(account)
                        
            elif choice == '7':
                print("\n🧹 ОЧИСТКА ПАМЯТИ")
                print("="*30)
                
                # Показываем текущее использование
                memory_info = bot.rate_limiter.get_memory_usage_info()
                print(f"Текущее использование: {memory_info['memory_usage_estimate_mb']:.2f} MB")
                print(f"Записей в памяти: {memory_info['total_message_records'] + memory_info['total_chat_records']}")
                
                confirm = input("Очистить всю историю лимитов? (y/n): ").strip().lower()
                if confirm == 'y':
                    bot.rate_limiter.cleanup_all_accounts()
                    
                    # Сбрасываем штрафы если нужно
                    reset_penalties = input("Сбросить штрафы аккаунтов? (y/n): ").strip().lower()
                    if reset_penalties == 'y':
                        for account_name in bot.account_manager.accounts.keys():
                            bot.rate_limiter.reset_account_penalties(account_name)
                    
                    print("✅ Память очищена!")
                    
                    # Показываем новое использование
                    new_memory_info = bot.rate_limiter.get_memory_usage_info()
                    print(f"Новое использование: {new_memory_info['memory_usage_estimate_mb']:.2f} MB")
                
            elif choice == '8':
                print("\n🔄 ПРОВЕРКА И ПЕРЕПОДКЛЮЧЕНИЕ АККАУНТОВ")
                print("="*50)
                
                # Проверяем здоровье подключений
                health_status = await bot.account_manager.check_connections_health()
                
                healthy_count = sum(health_status.values())
                total_count = len(health_status)
                
                print(f"Здоровых подключений: {healthy_count}/{total_count}")
                
                for account_name, is_healthy in health_status.items():
                    status_icon = "✅" if is_healthy else "❌"
                    print(f"  {status_icon} {account_name}")
                
                if healthy_count < total_count:
                    reconnect = input(f"\nПереподключить {total_count - healthy_count} неудачных аккаунтов? (y/n): ").strip().lower()
                    if reconnect == 'y':
                        reconnected = await bot.account_manager.auto_reconnect_failed(bot.api_id, bot.api_hash)
                        print(f"✅ Переподключено: {reconnected} аккаунтов")
                else:
                    print("✅ Все аккаунты работают нормально!")
                
            elif choice == '9':
                break
                
            else:
                print("Неверный выбор")
    
    except KeyboardInterrupt:
        print("\nПрерывание пользователем")
        bot.stop_sending()
    
    finally:
        await bot.account_manager.disconnect_all()

if __name__ == "__main__":
    asyncio.run(main())