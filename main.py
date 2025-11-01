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

class TelegramBot:
    """Главный класс бота для управления рассылкой"""
    
    def __init__(self):
        self.setup_logging()
        
        self.account_manager = AccountManager()
        self.rate_limiter = RateLimiter()
        self.message_queue = MessageQueue()
        self.sender = MessageSender()
        
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
        
        # Загружаем аккаунты
        if not self.account_manager.load_accounts():
            self.logger.error("Не удалось загрузить аккаунты")
            return False
        
        # Подключаем аккаунты
        connected_accounts = 0
        for account_name in self.account_manager.accounts.keys():
            if await self.account_manager.connect_account(account_name, self.api_id, self.api_hash):
                connected_accounts += 1
        
        if connected_accounts == 0:
            self.logger.error("Не удалось подключить ни одного аккаунта")
            return False
        
        self.logger.info(f"Подключено {connected_accounts} аккаунтов")
        
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
            while self.is_running and not self.message_queue.message_queue.empty():
                await self.process_message_batch()
                
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
        
        # Обрабатываем задачи для каждого активного аккаунта
        tasks = []
        for account_name in active_accounts:
            task = self.message_queue.get_next_task()
            if task and task.account_name == account_name:
                tasks.append(self.process_single_message(task))
            elif task:
                # Возвращаем задачу в очередь если она для другого аккаунта
                self.message_queue.message_queue.put(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def process_single_message(self, task: MessageTask):
        """Обработка одного сообщения"""
        account_name = task.account_name
        
        try:
            # Проверяем лимиты
            can_send, wait_time = self.rate_limiter.can_send_message(account_name, task.is_new_chat)
            
            if not can_send:
                self.logger.info(f"Лимит для {account_name}, ждем {wait_time:.1f}с")
                await asyncio.sleep(wait_time)
                # Возвращаем задачу в очередь
                self.message_queue.message_queue.put(task)
                return
            
            # Получаем клиент аккаунта
            account_data = self.account_manager.accounts[account_name]
            client = account_data['client']
            
            if not client:
                self.logger.error(f"Клиент для {account_name} не найден")
                self.message_queue.requeue_failed_task(task)
                return
            
            # Отправляем сообщение
            result = await self.sender.send_message(client, task)
            
            # Анализируем результат
            analysis = self.sender.analyze_send_result(result)
            
            if result['success']:
                # Успешная отправка
                self.rate_limiter.record_message_sent(account_name, task.is_new_chat)
                self.message_queue.mark_task_completed(task)
                self.stats['messages_sent'] += 1
                
                # Умная задержка
                await self.rate_limiter.smart_delay(account_name)
                
            else:
                # Обработка ошибки
                await self.handle_send_error(task, result, analysis)
            
        except Exception as e:
            self.logger.error(f"Ошибка обработки сообщения для {account_name}: {e}")
            self.message_queue.requeue_failed_task(task)
    
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
            print("5. Выход")
            
            choice = input("Выберите действие: ").strip()
            
            if choice == '1':
                max_msg = input("Максимум сообщений (Enter для всех): ").strip()
                max_messages = int(max_msg) if max_msg.isdigit() else None
                await bot.start_sending(max_messages)
                
            elif choice == '2':
                stats = bot.account_manager.get_account_stats()
                print(json.dumps(stats, indent=2, ensure_ascii=False))
                
            elif choice == '3':
                for account_name in bot.account_manager.accounts.keys():
                    limits = bot.rate_limiter.get_account_limits_info(account_name)
                    print(f"{account_name}: {limits}")
                    
            elif choice == '4':
                for account_name, data in bot.account_manager.accounts.items():
                    if data['client']:
                        result = await bot.sender.test_account_connection(data['client'], account_name)
                        print(f"{account_name}: {'OK' if result['success'] else 'ERROR'}")
                        
            elif choice == '5':
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