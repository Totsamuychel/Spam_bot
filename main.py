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

# Импорт dotenv для загрузки переменных окружения
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

# Импорт aioconsole для неблокирующего ввода
try:
    import aioconsole
    AIOCONSOLE_AVAILABLE = True
except ImportError:
    AIOCONSOLE_AVAILABLE = False
    print("⚠️ aioconsole не установлен. Используйте: pip install aioconsole")
    print("Будет использован обычный input() (может блокировать async операции)")

# Добавляем src в путь для импортов
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

# Импортируем модули с обработкой ошибок
try:
    from telethon import TelegramClient
    from src.account_manager import AccountManager
    from src.rate_limiter import RateLimiter
    from src.message_queue import MessageQueue, MessageTask
    from src.sender import MessageSender
    from src.auth_manager import AuthManager
    from src.smart_scheduler import SmartScheduler
    from src.member_collector import MemberCollector

except ImportError as e:
    print(f"❌ Ошибка импорта модулей: {e}")
    print("Убедитесь что все файлы находятся в папке src/")
    sys.exit(1)

async def async_input(prompt: str) -> str:
    """Неблокирующий ввод с поддержкой aioconsole"""
    if AIOCONSOLE_AVAILABLE:
        return await aioconsole.ainput(prompt)
    else:
        # Fallback к обычному input (может блокировать)
        return input(prompt)

def load_message_from_file(file_path: str = "message.txt") -> str:
    """Загрузить сообщение из файла с поиском в нескольких местах"""
    try:
        # Список возможных путей к файлу сообщения
        possible_paths = [
            file_path,                    # Указанный путь
            f"data/{file_path}",         # В папке data
            f"data/message.txt",         # Стандартное имя в data
            "message.txt",               # В корневой папке
            "data/message.txt.example"   # Пример файла
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        logging.getLogger(__name__).info(f"📄 Загружено сообщение из {path}")
                        return content
        
        return None
    except Exception as e:
        logging.getLogger(__name__).error(f"Ошибка загрузки сообщения: {e}")
        return None

def save_message_to_file(message: str, file_path: str = "message.txt") -> bool:
    """Сохранить сообщение в файл"""
    try:
        # Если путь не содержит папку, сохраняем в data/
        if "/" not in file_path and "\\" not in file_path:
            # Создаем папку data если её нет
            os.makedirs("data", exist_ok=True)
            file_path = f"data/{file_path}"
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(message)
        
        logging.getLogger(__name__).info(f"💾 Сообщение сохранено в {file_path}")
        return True
    except Exception as e:
        logging.getLogger(__name__).error(f"Ошибка сохранения сообщения в {file_path}: {e}")
        return False

class TelegramBot:
    """Главный класс бота для управления рассылкой"""
    
    def __init__(self):
        self.setup_logging()
        
        self.account_manager = AccountManager()
        self.rate_limiter = RateLimiter()
        self.message_queue = MessageQueue()
        self.sender = MessageSender()
        self.auth_manager = None  # Инициализируется после загрузки конфига
        self.scheduler = SmartScheduler()  # Умный планировщик отправки
        
        self.is_running = False
        self.stats = {
            'messages_sent': 0,
            'messages_failed': 0,
            'accounts_blocked': 0,
            'start_time': None
        }
        self._stats_lock = asyncio.Lock()  # Защита статистики от race conditions
        
        # Настройки API (нужно заполнить)
        self.api_id = None
        self.api_hash = None
        
    def setup_logging(self):
        """Настройка логирования - только в файл, без вывода в консоль"""
        # Создаем форматтер для логов
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Настраиваем файловый обработчик
        file_handler = logging.FileHandler('bot.log', encoding='utf-8')
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)  # В файл записываем все
        
        # Настраиваем консольный обработчик только для критических ошибок
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter('❌ %(message)s'))
        console_handler.setLevel(logging.ERROR)  # В консоль только ошибки
        
        # Настраиваем корневой логгер
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.handlers.clear()  # Очищаем существующие обработчики
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
        # Отключаем логи telethon в консоли (они будут только в файле)
        telethon_logger = logging.getLogger('telethon')
        telethon_logger.setLevel(logging.WARNING)  # Только предупреждения и ошибки
        
        self.logger = logging.getLogger(__name__)
    
    def load_config(self):
        """Загрузка конфигурации с приоритетом .env файла"""
        try:
            # Определяем путь к .env файлу (рядом с исполняемым файлом)
            if getattr(sys, 'frozen', False):
                # Если запущено как exe
                app_dir = os.path.dirname(sys.executable)
            else:
                # Если запущено как скрипт
                app_dir = os.path.dirname(os.path.abspath(__file__))
            
            env_path = os.path.join(app_dir, '.env')
            
            # Сначала пытаемся загрузить из .env файла
            if DOTENV_AVAILABLE and os.path.exists(env_path):
                load_dotenv(env_path)
                env_api_id = os.getenv('API_ID')
                env_api_hash = os.getenv('API_HASH')
                
                if env_api_id and env_api_hash:
                    try:
                        self.api_id = int(env_api_id)
                        self.api_hash = env_api_hash
                                                
                        # Создаем config.json для совместимости
                        config = {
                            "api_id": self.api_id,
                            "api_hash": self.api_hash,
                            "source": "env_file",
                            "created_at": "auto-generated from .env"
                        }
                        
                        with open('config.json', 'w', encoding='utf-8') as f:
                            json.dump(config, f, indent=2, ensure_ascii=False)
                        
                        return True
                    except ValueError:
                        self.logger.warning("Неверный формат API_ID в .env файле")
            
            # Проверяем существует ли config.json
            if os.path.exists('config.json'):
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.api_id = config.get('api_id')
                    self.api_hash = config.get('api_hash')
                    
                    # Проверяем что данные не являются примерами
                    if (self.api_id and self.api_hash and 
                        str(self.api_id) != "12345" and 
                        self.api_hash != "your_api_hash_here"):
                        return True
            
            # Если ни .env, ни config.json не содержат валидных данных - создаем автоматически
            return self.create_config_automatically()
                
        except Exception as e:
            self.logger.error(f"Ошибка загрузки конфигурации: {e}")
            return self.create_config_automatically()
    
    def create_config_automatically(self):
        """Автоматическое создание конфигурации с предустановленными API данными"""
        try:
            # Пытаемся загрузить реальные API данные из отдельного файла
            try:
                from api_config import REAL_API_ID, REAL_API_HASH
                api_id = REAL_API_ID
                api_hash = REAL_API_HASH
                self.logger.info("Используются реальные API данные из api_config.py")
            except ImportError:
                # Если файла нет, используем placeholder данные
                api_id = 12345  # Замените на ваш API ID
                api_hash = "your_api_hash_here"  # Замените на ваш API Hash
                self.logger.warning("api_config.py не найден, используются placeholder данные")
            
            self.logger.info("Создание конфигурации с предустановленными API данными")
            
            # Определяем путь для .env файла
            if getattr(sys, 'frozen', False):
                # Если запущено как exe
                app_dir = os.path.dirname(sys.executable)
            else:
                # Если запущено как скрипт
                app_dir = os.path.dirname(os.path.abspath(__file__))
            
            env_path = os.path.join(app_dir, '.env')
            
            # Создаем .env файл
            env_content = f"API_ID={api_id}\nAPI_HASH={api_hash}\n"
            
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write(env_content)
            
            self.logger.info(f".env файл создан: {env_path}")
            
            # Создаем config.json для совместимости
            config = {
                "api_id": api_id,
                "api_hash": api_hash,
                "source": "auto_created",
                "created_at": "auto-generated with preset values"
            }
            
            config_path = os.path.join(app_dir, 'config.json')
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"config.json создан: {config_path}")
            
            # Устанавливаем значения
            self.api_id = api_id
            self.api_hash = api_hash
            
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка автоматического создания конфигурации: {e}")
            # Если автоматическое создание не удалось, переходим к интерактивному
            return self.create_config_interactive()
    
    def create_config_interactive(self):
        """Интерактивное создание конфигурации (резервный вариант)"""
        print("\n" + "="*60)
        print("🔧 ПЕРВОНАЧАЛЬНАЯ НАСТРОЙКА")
        print("="*60)
        print("Для работы бота необходимы API данные от Telegram.")
        print("Если у вас их нет, получите их на https://my.telegram.org/")
        print()
        print("📋 Инструкция:")
        print("1. Перейдите на https://my.telegram.org/")
        print("2. Войдите в свой аккаунт Telegram")
        print("3. Перейдите в 'API development tools'")
        print("4. Создайте новое приложение")
        print("5. Скопируйте api_id и api_hash")
        print("="*60)
        
        try:
            # Запрашиваем API ID
            while True:
                api_id_input = input("\n🔑 Введите ваш API ID: ").strip()
                if api_id_input.isdigit():
                    api_id = int(api_id_input)
                    break
                else:
                    print("❌ API ID должен быть числом. Попробуйте еще раз.")
            
            # Запрашиваем API Hash
            while True:
                api_hash = input("🔐 Введите ваш API Hash: ").strip()
                if len(api_hash) >= 32:  # API Hash обычно длинный
                    break
                else:
                    print("❌ API Hash слишком короткий. Проверьте правильность ввода.")
            
            # Создаем конфигурацию
            config = {
                "api_id": api_id,
                "api_hash": api_hash,
                "created_at": "auto-generated",
                "version": "1.0"
            }
            
            # Сохраняем в файл
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            print("\n✅ Конфигурация сохранена в config.json")
            print("🔒 Храните этот файл в безопасности!")
            
            # Устанавливаем значения
            self.api_id = api_id
            self.api_hash = api_hash
            
            return True
            
        except KeyboardInterrupt:
            print("\n❌ Настройка прервана пользователем")
            return False
        except Exception as e:
            print(f"\n❌ Ошибка создания конфигурации: {e}")
            return False
    
    async def initialize(self):
        """Инициализация всех компонентов"""
        self.logger.info("Инициализация бота")
        
        # Загружаем конфигурацию
        if not self.load_config():
            return False
        
        # Инициализируем менеджер авторизации
        self.auth_manager = AuthManager(self.api_id, self.api_hash)
        
        # Инициализируем сборщик участников
        self.member_collector = MemberCollector(self.api_id, self.api_hash)
        
        # Очищаем поврежденные сессии перед загрузкой
        cleaned_sessions = self.account_manager.cleanup_corrupted_sessions()
        if cleaned_sessions > 0:
            print(f"🧹 Автоматически очищено {cleaned_sessions} поврежденных session файлов")
        
        # Загружаем аккаунты
        if not self.account_manager.load_accounts():
            print("\n=== By Donut company INC. ===")
            print("Добро пожаловать в TELEGRAM MULTY\n")
            print("Давайте добавим ваш первый аккаунт для рассылки!")
            
            await async_input("Нажмите Enter чтобы добавить аккаунт ")
            success = await self.auth_manager.add_new_account()
            if success:
                # Перезагружаем аккаунты
                if not self.account_manager.load_accounts():
                    self.logger.error("Не удалось загрузить аккаунты после добавления")
                    return False
                # Синхронизируем scheduler после добавления аккаунта
                await self.sync_scheduler_with_accounts()
            else:
                self.logger.error("Не удалось добавить аккаунт")
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
                        self.logger.info(f"Подключен аккаунт {account_name}: {account_info}")
                    except Exception as e:
                        self.logger.warning(f"Не удалось получить информацию об аккаунте {account_name}: {e}")
                        self.logger.info(f"Подключен аккаунт {account_name}")
        
        if connected_accounts == 0:
            self.logger.error("Не удалось подключить ни одного аккаунта")
            return False
        
        self.logger.info(f"Всего подключено {connected_accounts} аккаунтов")
        
        # Инициализируем SmartScheduler с активными аккаунтами
        for account_name in self.account_manager.accounts.keys():
            if self.account_manager.accounts[account_name]['is_active']:
                await self.scheduler.add_account(account_name)
                self.logger.info(f"Аккаунт {account_name} добавлен в планировщик")
        
        # Загружаем данные сообщений или запускаем сбор канала
        if not self.message_queue.load_messages_data():
            self.logger.info("Файл с данными получателей не найден")
            print("\n" + "="*60)
            print("ДАННЫЕ ПОЛУЧАТЕЛЕЙ НЕ НАЙДЕНЫ")
            print("="*60)
            print("Для начала работы необходимо собрать список получателей.")
            print("Используйте пункт меню '4. Собрать участников из Telegram каналов'")
            print("="*60)
        
        return True
    
    async def start_sending(self, max_messages: int = None):
        """Запуск процесса рассылки"""
        if self.is_running:
            self.logger.warning("Рассылка уже запущена")
            return
        
        self.is_running = True
        await self.update_stats(start_time=asyncio.get_event_loop().time())
        
        try:
            # Получаем активные аккаунты
            active_accounts = await self.account_manager.get_active_accounts_list()
            
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
            batch_count = 0
            total_processed = 0
            
            self.logger.info(f"📊 Начальное состояние очереди: {self.message_queue.message_queue.qsize()} задач")
            
            while self.is_running and not self.message_queue.message_queue.empty():
                batch_count += 1
                queue_size_before = self.message_queue.message_queue.qsize()
                
                self.logger.info(f"🔄 Обработка батча #{batch_count}, задач в очереди: {queue_size_before}")
                
                await self.process_message_batch()
                
                queue_size_after = self.message_queue.message_queue.qsize()
                processed_in_batch = queue_size_before - queue_size_after
                total_processed += processed_in_batch
                
                self.logger.info(f"✅ Батч #{batch_count} завершен: обработано {processed_in_batch} задач, осталось {queue_size_after}")
                
                # Очистка памяти каждые 100 сообщений
                if total_processed > 0 and total_processed % 100 == 0:
                    self.rate_limiter.cleanup_all_accounts()
                    self.logger.info(f"🧹 Очистка памяти после {total_processed} сообщений")
                
                # Проверка здоровья подключений каждые 50 сообщений
                if total_processed > 0 and total_processed % 50 == 0:
                    try:
                        reconnected = await self.account_manager.auto_reconnect_failed(self.api_id, self.api_hash)
                        if reconnected > 0:
                            self.logger.info(f"🔄 Переподключено {reconnected} аккаунтов")
                            # Синхронизируем scheduler после переподключения
                            await self.sync_scheduler_with_accounts()
                    except Exception as e:
                        self.logger.warning(f"Ошибка при проверке здоровья подключений: {e}")
                
                # Небольшая пауза между батчами (некритическая операция)
                import random
                await asyncio.sleep(random.uniform(0.2, 1.0))
            
            # Завершение рассылки
            await self.finish_sending()
            
        except Exception as e:
            self.logger.error(f"Ошибка в процессе рассылки: {e}")
        finally:
            self.is_running = False
    
    async def process_message_batch(self):
        """Обработка батча сообщений"""
        # Получаем активные аккаунты
        active_accounts = await self.account_manager.get_active_accounts_list()
        
        if not active_accounts:
            self.logger.warning("Нет активных аккаунтов, останавливаем рассылку")
            self.is_running = False
            return
        
        # Обрабатываем все доступные задачи в этом батче
        tasks_processed = 0
        max_batch_size = len(active_accounts)  # Обрабатываем столько задач, сколько у нас аккаунтов
        
        for _ in range(max_batch_size):
            task = self.message_queue.get_next_task()
            if task:
                await self.process_single_message(task)
                tasks_processed += 1
            else:
                # Если задач больше нет, завершаем
                break
        
        if tasks_processed == 0:
            # Если не обработали ни одной задачи, завершаем рассылку
            self.is_running = False
            self.logger.info("Все задачи обработаны, завершаем рассылку")
    
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
                await self.update_stats(messages_failed=1)
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
                self.account_manager.update_message_sent(account_name)  # Обновляем статистику аккаунта
                self.message_queue.mark_task_completed(task)
                await self.update_stats(messages_sent=1)
                
                # Планируем следующую отправку через SmartScheduler
                await self.scheduler.schedule_next_send(account_name, is_new_chat=task.is_new_chat)
                
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
            await self.update_stats(messages_failed=1)
    
    async def handle_send_error(self, task: MessageTask, result: Dict, analysis: Dict):
        """Обработка ошибок отправки"""
        account_name = task.account_name
        
        # Критические ошибки - блокируем аккаунт
        if result.get('should_block_account', False):
            await self.account_manager.mark_account_blocked(account_name, result.get('error', 'unknown'))
            self.rate_limiter.record_account_blocked(account_name, result.get('error', 'unknown'))
            await self.update_stats(accounts_blocked=1)
            
            # Деактивируем аккаунт в scheduler
            await self.scheduler.deactivate_account(account_name, result.get('error', 'unknown'))
            
            # Перераспределяем задачи с заблокированного аккаунта
            active_accounts = await self.account_manager.get_active_accounts_list()
            self.message_queue.redistribute_tasks(account_name, active_accounts)
        
        # Ошибки с ожиданием (FloodWait, PeerFlood)
        elif analysis['should_wait']:
            # Определяем тип штрафа
            error_text = result.get('error', '').lower()
            if 'flood' in error_text:
                if 'peer' in error_text:
                    penalty_type = "peer_flood"
                else:
                    penalty_type = "flood_wait"
            else:
                penalty_type = "rate_limit"
            
            # Применяем штраф через scheduler
            await self.scheduler.apply_penalty(account_name, penalty_type)
            
            await asyncio.sleep(min(analysis['wait_time'], 300))  # Максимум 5 минут
            self.message_queue.requeue_failed_task(task)
        
        # Обычные ошибки - повторяем попытку
        elif result.get('should_retry', True):
            self.message_queue.requeue_failed_task(task)
        
        await self.update_stats(messages_failed=1)
    
    async def finish_sending(self):
        """Завершение процесса рассылки"""
        self.logger.info("Рассылка завершена")
        
        # Сохраняем неудачные сообщения
        self.message_queue.save_failed_messages()
        
        # Выводим статистику
        await self.print_final_stats()
        
        # НЕ отключаем аккаунты после рассылки - оставляем их подключенными для следующих рассылок
        self.logger.info("🔗 Аккаунты остаются подключенными для следующих рассылок")
    
    async def print_final_stats(self):
        """Вывод финальной статистики"""
        queue_stats = self.message_queue.get_queue_stats()
        account_stats = self.account_manager.get_account_stats()
        
        # Получаем безопасную копию статистики
        stats = await self.get_stats_copy()
        
        elapsed_time = asyncio.get_event_loop().time() - stats['start_time']
        
        print("\n" + "="*70)
        print("🎯 ИТОГОВАЯ СТАТИСТИКА РАССЫЛКИ")
        print("="*70)
        
        # Основные показатели
        print(f"⏱️  Время выполнения: {elapsed_time:.1f} секунд")
        print(f"✅ Отправлено сообщений: {stats['messages_sent']}")
        print(f"❌ Неудачных отправок: {stats['messages_failed']}")
        print(f"🚫 Заблокировано аккаунтов: {stats['accounts_blocked']}")
        
        # Процент успеха с цветовой индикацией
        success_rate = queue_stats['completion_rate']
        if success_rate >= 90:
            success_icon = "🟢"
        elif success_rate >= 70:
            success_icon = "🟡"
        else:
            success_icon = "🔴"
        
        print(f"{success_icon} Процент успеха: {success_rate:.1f}%")
        
        # Информация об аккаунтах
        print(f"📱 Активных аккаунтов: {account_stats['active_accounts']}")
        
        # Скорость рассылки
        if elapsed_time > 0:
            messages_per_minute = (stats['messages_sent'] / elapsed_time) * 60
            print(f"🚀 Скорость: {messages_per_minute:.1f} сообщений/мин")
        
        # Дополнительная статистика
        total_messages = self.stats['messages_sent'] + self.stats['messages_failed']
        if total_messages > 0:
            print(f"📊 Всего обработано: {total_messages} сообщений")
        
        print("="*70)
        
        # Также логируем для истории
        self.logger.info("=== СТАТИСТИКА РАССЫЛКИ ===")
        self.logger.info(f"Время выполнения: {elapsed_time:.1f} секунд")
        self.logger.info(f"Отправлено сообщений: {self.stats['messages_sent']}")
        self.logger.info(f"Неудачных отправок: {self.stats['messages_failed']}")
        self.logger.info(f"Заблокировано аккаунтов: {self.stats['accounts_blocked']}")
        self.logger.info(f"Процент успеха: {queue_stats['completion_rate']:.1f}%")
        self.logger.info(f"Активных аккаунтов: {account_stats['active_accounts']}")
    

    
    async def sync_scheduler_with_accounts(self):
        """Синхронизация SmartScheduler с текущим состоянием аккаунтов"""
        try:
            # Получаем текущие аккаунты из scheduler
            scheduler_accounts = set(self.scheduler.account_schedules.keys())
            
            # Получаем активные аккаунты из account_manager
            current_accounts = set()
            for account_name, account_data in self.account_manager.accounts.items():
                if account_data['is_active']:
                    current_accounts.add(account_name)
            
            # Добавляем новые аккаунты в scheduler
            for account_name in current_accounts - scheduler_accounts:
                await self.scheduler.add_account(account_name)
                self.logger.info(f"📅 Аккаунт {account_name} добавлен в планировщик")
            
            # Удаляем неактивные аккаунты из scheduler
            for account_name in scheduler_accounts - current_accounts:
                await self.scheduler.remove_account(account_name)
                self.logger.info(f"📅 Аккаунт {account_name} удален из планировщика")
            
            self.logger.info(f"📅 Планировщик синхронизирован: {len(current_accounts)} активных аккаунтов")
            
        except Exception as e:
            self.logger.error(f"Ошибка синхронизации планировщика: {e}")
    
    async def unblock_account_with_sync(self, account_name: str):
        """Разблокировать аккаунт и синхронизировать с scheduler"""
        try:
            await self.account_manager.unblock_account(account_name)
            # Синхронизируем scheduler после разблокировки
            await self.sync_scheduler_with_accounts()
            self.logger.info(f"🔓 Аккаунт {account_name} разблокирован и синхронизирован с планировщиком")
        except Exception as e:
            self.logger.error(f"Ошибка разблокировки аккаунта {account_name}: {e}")
    
    async def reconnect_account_with_sync(self, account_name: str):
        """Переподключить аккаунт и синхронизировать с scheduler"""
        try:
            success = await self.account_manager.reconnect_account(account_name, self.api_id, self.api_hash)
            if success:
                # Синхронизируем scheduler после переподключения
                await self.sync_scheduler_with_accounts()
                self.logger.info(f"🔄 Аккаунт {account_name} переподключен и синхронизирован с планировщиком")
            return success
        except Exception as e:
            self.logger.error(f"Ошибка переподключения аккаунта {account_name}: {e}")
            return False
    
    async def auto_collect_channel_participants(self) -> bool:
        """Автоматический сбор участников канала при отсутствии данных"""
        try:
            # Проверяем есть ли активные аккаунты
            active_accounts = await self.account_manager.get_active_accounts_list()
            if not active_accounts:
                print("❌ Нет активных аккаунтов для сбора участников")
                print("Сначала необходимо подключить хотя бы один аккаунт")
                return False
            
            print("\n📥 АВТОМАТИЧЕСКИЙ СБОР УЧАСТНИКОВ")
            print("="*50)
            print("Выберите способ сбора участников:")
            print("1. По username или ссылке (быстрый)")
            print("2. По названию публичной группы/канала (с поиском)")
            
            while True:
                choice = await async_input("Ваш выбор (1/2): ")
                choice = choice.strip()
                
                if choice == '1':
                    # Способ 1: По username/ссылке
                    print("\n📋 Поддерживаемые форматы:")
                    print("   • Ссылка: https://t.me/channelname")
                    print("   • Ссылка: t.me/channelname") 
                    print("   • Username: @channelname")
                    print("   • Username: channelname")
                    
                    while True:
                        channel = await async_input("\n🔗 Введите ссылку на канал или его username: ")
                        channel = channel.strip()
                        
                        if not channel:
                            print("❌ Ссылка на канал не может быть пустой")
                            retry = await async_input("Попробовать еще раз? (y/n): ")
                            if retry.strip().lower() != 'y':
                                return False
                            continue
                        
                        # Валидация ввода
                        if not MemberCollector.validate_channel_input(channel):
                            print("❌ Неверный формат ссылки или username канала")
                            print("Примеры правильных форматов:")
                            print("  • https://t.me/channelname")
                            print("  • t.me/channelname")
                            print("  • @channelname")
                            print("  • channelname")
                            retry = await async_input("Попробовать еще раз? (y/n): ")
                            if retry.strip().lower() != 'y':
                                return False
                            continue
                        
                        break
                    
                    # Передаем авторизованный клиент
                    first_account = active_accounts[0]
                    account_data = self.account_manager.accounts[first_account]
                    
                    if account_data['client'] and account_data['is_active']:
                        self.member_collector.set_external_client(account_data['client'])
                    
                    # Используем новый модуль для сбора по username
                    success = await self.member_collector._collect_members_from_channel(channel)
                    break
                    
                elif choice == '2':
                    # Способ 2: По названию, username или ссылке
                    print("\n🔍 Сбор людей по названию ТГК, USERNAME ИЛИ ССЫЛКЕ")
                    print("-" * 50)
                    print("Поддерживаемые форматы ввода:")
                    print("• Название: TestMarket")
                    print("• Username: @TestMarkett")
                    print("• Ссылка: https://t.me/TestMarkett")
                    print("• Ссылка: t.me/TestMarkett")
                    
                    while True:
                        user_input = await async_input("\n📝 Введите название, юзернейм (@имя) или ссылку группы/канала: ")
                        user_input = user_input.strip()
                        
                        if not user_input:
                            print("❌ Ввод не может быть пустым")
                            continue
                        
                        if len(user_input) < 3:
                            print("❌ Ввод слишком короткий (минимум 3 символа)")
                            continue
                        
                        break
                    
                    # Передаем авторизованный клиент
                    first_account = active_accounts[0]
                    account_data = self.account_manager.accounts[first_account]
                    
                    if account_data['client'] and account_data['is_active']:
                        self.member_collector.set_external_client(account_data['client'])
                    
                    # Используем новый модуль для сбора по любому типу ввода
                    success = await self.member_collector.collect_members_by_input_async(user_input)
                    break
                    
                else:
                    print("❌ Неверный выбор. Введите 1 или 2")
                    continue
            
            if success:
                print("✅ Сбор участников завершен успешно!")
                print("📁 Данные сохранены в data/messages_data.json")
                return True
            else:
                print("❌ Не удалось собрать участников")
                return False
                
        except Exception as e:
            self.logger.error(f"Ошибка автоматического сбора: {e}", exc_info=True)
            print(f"❌ Ошибка: {e}")
            return False
    
    def stop_sending(self):
        """Остановка рассылки"""
        self.logger.info("Получен сигнал остановки рассылки")
        self.is_running = False
    
    async def get_stats_copy(self) -> Dict:
        """Получить безопасную копию статистики"""
        async with self._stats_lock:
            return self.stats.copy()
    
    async def update_stats(self, **kwargs):
        """Безопасное обновление статистики"""
        async with self._stats_lock:
            for key, value in kwargs.items():
                if key in self.stats:
                    if isinstance(value, int) and key != 'start_time':
                        self.stats[key] += value  # Инкремент для счетчиков
                    else:
                        self.stats[key] = value  # Прямое присвоение для времени
    
    async def account_management_menu(self):
        """Подменю управления аккаунтами"""
        while True:
            print("\n🔐 НАСТРОЙКИ")
            print("="*50)
            print("1. Показать лимиты аккаунтов")
            print("2. Очистить память и сбросить лимиты")
            print("3. Проверить и переподключить аккаунты")
            print("4. Очистить поврежденные сессии")
            print("5. Синхронизировать планировщик")
            print("6. Поменять TG API ID, HASH")
            print("7. Переименовать название сессий")
            print("0. Вернуться в главное меню")
            
            choice = await async_input("Выберите действие: ")
            choice = choice.strip()
            
            if choice == '1':
                # Показать лимиты аккаунтов
                print("\n📊 ЛИМИТЫ И ИСПОЛЬЗОВАНИЕ ПАМЯТИ")
                print("="*60)
                
                # Информация об использовании памяти
                memory_info = self.rate_limiter.get_memory_usage_info()
                print(f"💾 ИСПОЛЬЗОВАНИЕ ПАМЯТИ:")
                print(f"   📱 Отслеживается аккаунтов: {memory_info['accounts_tracked']}")
                print(f"   📤 Записей сообщений: {memory_info['total_message_records']}")
                print(f"   💬 Записей чатов: {memory_info['total_chat_records']}")
                print(f"   🧠 Примерное использование: {memory_info['memory_usage_estimate_mb']:.2f} МБ")
                print(f"   ⚠️ Аккаунтов со штрафами: {memory_info['accounts_with_penalties']}")
                
                print(f"\n📋 ЛИМИТЫ ПО АККАУНТАМ:")
                print("-"*60)
                
                if not self.account_manager.accounts:
                    print("📭 Нет загруженных аккаунтов")
                else:
                    for account_name in self.account_manager.accounts.keys():
                        limits = self.rate_limiter.get_account_limits_info_russian(account_name)
                        print(f"\n📞 {account_name}:")
                        
                        # Выводим информацию о лимитах на русском языке
                        for key, value in limits.items():
                            if key == 'Сообщений в минуту':
                                print(f"   ⚡ {key}: {value}")
                            elif key == 'Сообщений в час':
                                print(f"   🕐 {key}: {value}")
                            elif key == 'Новых чатов в день':
                                print(f"   💬 {key}: {value}")
                            elif key == 'Штрафы':
                                if value > 0:
                                    print(f"   ⚠️ {key}: {value}")
                                else:
                                    print(f"   ✅ {key}: Нет")
                            elif key == 'Может отправлять сейчас':
                                print(f"   🚀 {key}: {value}")
                            else:
                                print(f"   {key}: {value}")
                
                print("\n" + "="*60)
                
            elif choice == '2':
                # Очистить память и сбросить лимиты
                print("\n🧹 ОЧИСТКА ПАМЯТИ")
                print("="*30)
                
                # Показываем текущее использование
                memory_info = self.rate_limiter.get_memory_usage_info()
                print(f"Текущее использование: {memory_info['memory_usage_estimate_mb']:.2f} MB")
                print(f"Записей в памяти: {memory_info['total_message_records'] + memory_info['total_chat_records']}")
                
                confirm = await async_input("Очистить всю историю лимитов? (y/n): ")
                if confirm.strip().lower() == 'y':
                    self.rate_limiter.cleanup_all_accounts()
                    
                    # Сбрасываем штрафы если нужно
                    reset_penalties = await async_input("Сбросить штрафы аккаунтов? (y/n): ")
                    if reset_penalties.strip().lower() == 'y':
                        for account_name in self.account_manager.accounts.keys():
                            self.rate_limiter.reset_account_penalties(account_name)
                        
                        # Синхронизируем scheduler после сброса штрафов
                        await self.sync_scheduler_with_accounts()
                        print("📅 Планировщик синхронизирован после сброса штрафов")
                    
                    print("✅ Память очищена!")
                    
                    # Показываем новое использование
                    new_memory_info = self.rate_limiter.get_memory_usage_info()
                    print(f"Новое использование: {new_memory_info['memory_usage_estimate_mb']:.2f} MB")
                
            elif choice == '3':
                # Проверить и переподключить аккаунты
                print("\n🔄 ПРОВЕРКА И ПЕРЕПОДКЛЮЧЕНИЕ АККАУНТОВ")
                print("="*50)
                
                # Проверяем здоровье подключений
                health_status = await self.account_manager.check_connections_health()
                
                healthy_count = sum(health_status.values())
                total_count = len(health_status)
                
                print(f"Здоровых подключений: {healthy_count}/{total_count}")
                
                for account_name, is_healthy in health_status.items():
                    status_icon = "✅" if is_healthy else "❌"
                    print(f"  {status_icon} {account_name}")
                
                if healthy_count < total_count:
                    reconnect = await async_input(f"\nПереподключить {total_count - healthy_count} неудачных аккаунтов? (y/n): ")
                    if reconnect.strip().lower() == 'y':
                        reconnected = await self.account_manager.auto_reconnect_failed(self.api_id, self.api_hash)
                        print(f"✅ Переподключено: {reconnected} аккаунтов")
                        # Синхронизируем scheduler после переподключения
                        await self.sync_scheduler_with_accounts()
                else:
                    print("✅ Все аккаунты работают нормально!")
            
            elif choice == '4':
                # Очистить поврежденные сессии
                print("\n🗑️ ОЧИСТКА ПОВРЕЖДЕННЫХ СЕССИЙ")
                print("="*50)
                
                print("🔍 Проверяем session файлы на повреждения...")
                cleaned_count = self.account_manager.cleanup_corrupted_sessions()
                
                if cleaned_count > 0:
                    print(f"🧹 Удалено {cleaned_count} поврежденных session файлов")
                    print("💡 Для этих аккаунтов потребуется повторная авторизация")
                    
                    # Перезагружаем аккаунты после очистки
                    print("🔄 Перезагружаем список аккаунтов...")
                    if self.account_manager.load_accounts():
                        print("✅ Список аккаунтов обновлен")
                        await self.sync_scheduler_with_accounts()
                    else:
                        print("⚠️ После очистки не осталось валидных аккаунтов")
                else:
                    print("✅ Поврежденных session файлов не найдено")
            
            elif choice == '5':
                # Принудительная синхронизация планировщика
                print("\n📅 СИНХРОНИЗАЦИЯ ПЛАНИРОВЩИКА")
                print("="*50)
                
                print("🔄 Выполняется синхронизация планировщика с аккаунтами...")
                await self.sync_scheduler_with_accounts()
                
                # Показываем статистику планировщика
                scheduler_count = len(self.scheduler.account_schedules)
                active_count = len([name for name, data in self.account_manager.accounts.items() if data['is_active']])
                
                print(f"📊 Результат синхронизации:")
                print(f"   📅 Аккаунтов в планировщике: {scheduler_count}")
                print(f"   ✅ Активных аккаунтов: {active_count}")
                
                if scheduler_count == active_count:
                    print("🎉 Планировщик полностью синхронизирован!")
                else:
                    print("⚠️ Обнаружено расхождение, проверьте состояние аккаунтов")
            
            elif choice == '6':
                # Поменять TG API ID, HASH
                print("\n🔑 ИЗМЕНЕНИЕ API ДАННЫХ")
                print("="*50)
                print("Текущие API данные:")
                print(f"   API ID: {self.api_id}")
                print(f"   API Hash: {self.api_hash[:10]}..." if self.api_hash else "   API Hash: Не установлен")
                print()
                
                confirm = await async_input("Изменить API данные? (y/n): ")
                if confirm.strip().lower() == 'y':
                    # Запрашиваем новый API ID
                    while True:
                        api_id_input = await async_input("\n🔑 Введите новый API ID: ")
                        api_id_input = api_id_input.strip()
                        if api_id_input.isdigit():
                            new_api_id = int(api_id_input)
                            break
                        else:
                            print("❌ API ID должен быть числом")
                    
                    # Запрашиваем новый API Hash
                    new_api_hash = await async_input("🔐 Введите новый API Hash: ")
                    new_api_hash = new_api_hash.strip()
                    
                    if not new_api_hash or len(new_api_hash) < 32:
                        print("❌ API Hash слишком короткий или пустой")
                        continue
                    
                    # Обновляем config.json
                    try:
                        config = {
                            "api_id": new_api_id,
                            "api_hash": new_api_hash,
                            "updated_at": "manually updated"
                        }
                        
                        with open('config.json', 'w', encoding='utf-8') as f:
                            json.dump(config, f, indent=2, ensure_ascii=False)
                        
                        # Обновляем .env если он существует
                        if getattr(sys, 'frozen', False):
                            app_dir = os.path.dirname(sys.executable)
                        else:
                            app_dir = os.path.dirname(os.path.abspath(__file__))
                        
                        env_path = os.path.join(app_dir, '.env')
                        if os.path.exists(env_path):
                            with open(env_path, 'w', encoding='utf-8') as f:
                                f.write(f"API_ID={new_api_id}\nAPI_HASH={new_api_hash}\n")
                            print("✅ .env файл обновлен")
                        
                        # Обновляем текущие значения
                        self.api_id = new_api_id
                        self.api_hash = new_api_hash
                        
                        print("✅ API данные успешно обновлены!")
                        print("⚠️ Потребуется переподключение всех аккаунтов")
                        
                        # Предлагаем переподключить аккаунты
                        reconnect = await async_input("\nПереподключить все аккаунты сейчас? (y/n): ")
                        if reconnect.strip().lower() == 'y':
                            # Отключаем все аккаунты
                            await self.account_manager.disconnect_all()
                            
                            # Переподключаем с новыми API данными
                            reconnected = 0
                            for account_name in list(self.account_manager.accounts.keys()):
                                print(f"🔄 Переподключение {account_name}...", end=" ")
                                if await self.account_manager.connect_account(account_name, new_api_id, new_api_hash):
                                    reconnected += 1
                                    print("✅")
                                else:
                                    print("❌")
                            
                            print(f"\n✅ Переподключено {reconnected} аккаунтов")
                            await self.sync_scheduler_with_accounts()
                        
                    except Exception as e:
                        print(f"❌ Ошибка обновления API данных: {e}")
                        self.logger.error(f"Ошибка обновления API данных: {e}")
            
            elif choice == '7':
                # Переименовать название сессий
                print("\n✏️ ПЕРЕИМЕНОВАНИЕ SESSION ФАЙЛОВ")
                print("="*50)
                
                # Показываем список текущих сессий
                sessions_dir = "sessions"
                if not os.path.exists(sessions_dir):
                    print("❌ Папка sessions не найдена")
                    continue
                
                session_files = [f.replace('.session', '') for f in os.listdir(sessions_dir) if f.endswith('.session')]
                
                if not session_files:
                    print("📭 Нет session файлов для переименования")
                    continue
                
                print("📋 Текущие session файлы:")
                for i, session_name in enumerate(session_files, 1):
                    print(f"   {i}. {session_name}")
                
                print()
                old_name = await async_input("Введите текущее имя сессии для переименования: ")
                old_name = old_name.strip()
                
                if old_name not in session_files:
                    print(f"❌ Session файл '{old_name}' не найден")
                    continue
                
                new_name = await async_input("Введите новое имя сессии: ")
                new_name = new_name.strip()
                
                if not new_name:
                    print("❌ Новое имя не может быть пустым")
                    continue
                
                if new_name in session_files:
                    print(f"❌ Session файл '{new_name}' уже существует")
                    continue
                
                # Переименовываем файл
                try:
                    old_path = os.path.join(sessions_dir, f"{old_name}.session")
                    new_path = os.path.join(sessions_dir, f"{new_name}.session")
                    
                    os.rename(old_path, new_path)
                    print(f"✅ Session файл переименован: {old_name} → {new_name}")
                    
                    # Перезагружаем аккаунты
                    print("🔄 Перезагружаем список аккаунтов...")
                    if self.account_manager.load_accounts():
                        print("✅ Список аккаунтов обновлен")
                        await self.sync_scheduler_with_accounts()
                    
                except Exception as e:
                    print(f"❌ Ошибка переименования: {e}")
                    self.logger.error(f"Ошибка переименования session файла: {e}")
                    
            elif choice == '0':
                break
            else:
                print("❌ Неверный выбор")

async def main():
    """Главная функция"""
    # Выводим приветствие сразу
    print("\n=== By Donut company INC. ===")
    print("Добро пожаловать в TELEGRAM MULTY\n")
    
    bot = TelegramBot()
    
    try:
        # Инициализация
        if not await bot.initialize():
            print("\n❌ Ошибка инициализации.")
            print("\nЧто нужно сделать:")
            print("1. Убедитесь что у вас есть API данные (config.json или .env файл)")
            print("2. Добавьте хотя бы один аккаунт через пункт меню '3. Управление аккаунтами'")
            print("3. Если есть поврежденные session файлы - удалите их через пункт '6' в меню управления")
            
            # Предлагаем удалить поврежденные session файлы
            try:
                delete_sessions = await async_input("\nУдалить все session файлы и начать заново? (y/n): ")
                if delete_sessions.strip().lower() == 'y':
                    sessions_dir = "sessions"
                    if os.path.exists(sessions_dir):
                        session_files = [f for f in os.listdir(sessions_dir) if f.endswith('.session')]
                        deleted_count = 0
                        
                        for session_file in session_files:
                            session_path = os.path.join(sessions_dir, session_file)
                            try:
                                os.remove(session_path)
                                deleted_count += 1
                                print(f"Удален: {session_file}")
                            except Exception as e:
                                print(f"Не удалось удалить {session_file}: {e}")
                        
                        if deleted_count > 0:
                            print(f"\n✅ Удалено {deleted_count} session файлов")
                            print("Перезапустите программу для добавления новых аккаунтов")
                        else:
                            print("Не найдено session файлов для удаления")
                    else:
                        print("Папка sessions не найдена")
            except KeyboardInterrupt:
                print("\nОперация прервана")
            
            return
        
        # Интерактивное меню
        while True:
            print("\n=== TELEGRAM MULTY ===")
            print("1. Мои аккаунты и статистика")
            print("2. Добавить/убрать аккаунты")
            print("3. Настройки")
            print("4. Собрать лидов из Telegram канала")
            print("5. Начать рассылку")
            print("0. Выход")
            
            choice = await async_input("Выберите действие: ")
            choice = choice.strip()
            
            if choice == '5':
                print("\n🚀 ПОДГОТОВКА К РАССЫЛКЕ")
                print("="*50)
                
                # Проверяем есть ли получатели
                if not hasattr(bot.message_queue, 'recipients') or not bot.message_queue.recipients:
                    print("❌ Нет загруженных получателей!")
                    print("\n📋 Варианты действий:")
                    print("  1. Собрать участников из Telegram канала (автоматически)")
                    print("  2. Использовать пункт 5 в главном меню")
                    print("  3. Поместить данные в data/messages_data.json вручную")
                    
                    choice = await async_input("\nСобрать участников канала сейчас? (y/n): ")
                    if choice.strip().lower() == 'y':
                        print("\n🔄 Запускаем сбор участников канала...")
                        success = await bot.auto_collect_channel_participants()
                        if success:
                            # Перезагружаем данные
                            if bot.message_queue.load_messages_data():
                                print(f"✅ Загружено {len(bot.message_queue.recipients)} получателей")
                                # Продолжаем с рассылкой
                            else:
                                print("❌ Не удалось загрузить собранные данные")
                                continue
                        else:
                            print("❌ Сбор не удался, попробуйте позже")
                            continue
                    else:
                        continue
                
                # Убираем вывод в консоль для чистоты интерфейса
                
                # Запрашиваем текст сообщения
                print("\n📝 Введите текст сообщения для рассылки:")
                print("(Оставьте пустым для автозагрузки из файла)")
                print("(Для многострочного текста завершите пустой строкой)")
                print("\n🔍 Поиск файла сообщения в:")
                print("   • data/message.txt")
                print("   • message.txt")
                print("   • data/message.txt.example")
                
                # Проверяем есть ли сообщение в файле
                default_message = load_message_from_file("message.txt")
                if default_message:
                    print(f"\n📄 Найдено сообщение ({len(default_message)} символов)")
                    print("Превью:", default_message[:100] + ("..." if len(default_message) > 100 else ""))
                else:
                    print("\n⚠️ Файл сообщения не найден - введите текст вручную")
                
                message_lines = []
                first_line = await async_input("")
                
                if first_line.strip() == "" and default_message:
                    # Используем сообщение из файла
                    message_text = default_message
                    print("✅ Загружено сообщение из файла")
                else:
                    # Вводим сообщение вручную
                    if first_line.strip():
                        message_lines.append(first_line)
                    
                    while True:
                        line = await async_input("")
                        if line.strip() == "":
                            break
                        message_lines.append(line)
                    
                    if not message_lines:
                        print("❌ Сообщение не может быть пустым")
                        continue
                    
                    message_text = "\n".join(message_lines)
                    
                    # Предлагаем сохранить в файл
                    save_choice = await async_input("Сохранить это сообщение в data/message.txt? (y/n): ")
                    if save_choice.strip().lower() == 'y':
                        if save_message_to_file(message_text):
                            print("✅ Сообщение сохранено в data/message.txt")
                
                # Обновляем сообщение в очереди
                bot.message_queue.message_text = message_text
                
                # Показываем превью
                print(f"\n📄 ПРЕВЬЮ СООБЩЕНИЯ:")
                print("-" * 40)
                print(message_text[:200] + ("..." if len(message_text) > 200 else ""))
                print("-" * 40)
                
                confirm = await async_input("Начать рассылку с этим сообщением? (y/n): ")
                if confirm.strip().lower() != 'y':
                    print("❌ Рассылка отменена")
                    continue
                
                max_msg = await async_input("Скольким пользователям отправить? (Enter для всех): ")
                max_messages = int(max_msg.strip()) if max_msg.strip().isdigit() else None
                
                await bot.start_sending(max_messages)
                
            elif choice == '1':
                bot.account_manager.print_account_stats_russian()
                
            elif choice == '2':
                # Добавить/убрать аккаунты
                await bot.auth_manager.interactive_account_management()
                # Перезагружаем аккаунты после изменений
                if not bot.account_manager.load_accounts():
                    print("❌ Не удалось перезагрузить аккаунты после изменений")
                else:
                    # Синхронизируем scheduler с обновленными аккаунтами
                    await bot.sync_scheduler_with_accounts()
                
            elif choice == '3':
                await bot.account_management_menu()
                    
            elif choice == '4':
                # Проверяем есть ли активные аккаунты
                active_accounts = await bot.account_manager.get_active_accounts_list()
                if not active_accounts:
                    print("❌ Нет активных аккаунтов для сбора участников")
                    print("Сначала добавьте хотя бы один аккаунт (пункт 3)")
                    continue
                
                try:
                    # Передаем авторизованный клиент первого активного аккаунта
                    first_account = active_accounts[0]
                    account_data = bot.account_manager.accounts[first_account]
                    
                    if account_data['client'] and account_data['is_active']:
                        bot.member_collector.set_external_client(account_data['client'])
                    
                    # Используем новый модуль сбора участников
                    success = await bot.member_collector.collect_members_menu()
                    
                    if success:
                        # Перезагружаем данные в очередь
                        if bot.message_queue.load_messages_data():
                            print("\n✅ Участники успешно собраны!")
                            
                            # Показываем статистику
                            recipients_count = len(bot.message_queue.recipients)
                            print(f"📊 Загружено пользователей: {recipients_count}")
                            
                            # Подсчитываем статистику
                            users_with_username = len([u for u in bot.message_queue.recipients if u.get('username')])
                            users_with_display_name = len([u for u in bot.message_queue.recipients if u.get('display_name')])
                            users_with_phone = len([u for u in bot.message_queue.recipients if u.get('phone')])
                            
                            print(f"   • С username: {users_with_username}")
                            print(f"   • С именем: {users_with_display_name}")
                            print(f"   • С телефоном: {users_with_phone}")
                            print("\n🎉 Теперь можете начать рассылку (пункт 1)")
                        else:
                            print("❌ Не удалось загрузить собранные данные")
                    
                except Exception as e:
                    print(f"❌ Ошибка: {e}")
                    bot.logger.error(f"Ошибка сбора участников: {e}", exc_info=True)
                
            elif choice == '0':
                break
                
            else:
                print("Неверный выбор")
    
    except KeyboardInterrupt:
        print("\nПрерывание пользователем")
        bot.stop_sending()
    
    finally:
        await bot.account_manager.disconnect_all()

def safe_input(prompt: str) -> str:
    """Безопасный ввод, работающий в exe"""
    try:
        if AIOCONSOLE_AVAILABLE:
            # В exe aioconsole может не работать, используем обычный input
            return input(prompt)
        else:
            return input(prompt)
    except:
        return input(prompt)

def wait_for_exit():
    """Ожидание перед закрытием программы"""
    try:
        safe_input("\nНажмите Enter для выхода...")
    except:
        import time
        time.sleep(3)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n❌ Программа прервана пользователем")
        wait_for_exit()
    except Exception as e:
        print(f"\n💥 Критическая ошибка: {e}")
        print("Проверьте логи для получения подробной информации.")
        wait_for_exit()
    except SystemExit:
        pass  # Нормальный выход
    else:
        # Программа завершилась нормально
        wait_for_exit()