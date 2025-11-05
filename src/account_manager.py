import os
import json
import logging
import asyncio
from typing import List, Dict, Optional
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeInvalidError,
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    AuthKeyInvalidError
)

class AccountManager:
    """Управление аккаунтами Telegram для мультиаккаунтной рассылки"""
    
    def __init__(self, sessions_dir: str = "sessions"):
        self.sessions_dir = sessions_dir
        self.accounts = {}
        self.blocked_accounts = set()
        self.current_account_index = 0
        self._account_lock = asyncio.Lock()  # Защита от race conditions
        self.logger = logging.getLogger(__name__)
    
    def _remove_corrupted_session(self, account_name: str, session_path: str, reason: str):
        """Удаление поврежденного session файла"""
        try:
            if os.path.exists(session_path):
                os.remove(session_path)
                self.logger.warning(f"Удален поврежденный session файл: {session_path}")
                self.logger.warning(f"Причина: {reason}")
                print(f"Аккаунт {account_name}: поврежденная сессия удалена")
                print(f"Причина: {reason}")
                print(f"Необходимо заново авторизовать аккаунт через меню '3. Управление аккаунтами'")
                return True
        except Exception as e:
            self.logger.error(f"Ошибка удаления поврежденной сессии {session_path}: {e}")
        return False
    
    def _check_session_file_integrity(self, session_path: str) -> bool:
        """Базовая проверка целостности session файла"""
        try:
            # Проверяем что файл существует и не пустой
            if not os.path.exists(session_path):
                return False
            
            file_size = os.path.getsize(session_path)
            if file_size < 100:  # Session файл должен быть больше 100 байт
                self.logger.warning(f"Session файл {session_path} слишком мал ({file_size} байт)")
                return False
            
            # Проверяем что это SQLite файл (session файлы Telethon - это SQLite)
            try:
                with open(session_path, 'rb') as f:
                    header = f.read(16)
                    if not header.startswith(b'SQLite format 3'):
                        self.logger.warning(f"Session файл {session_path} не является SQLite базой")
                        return False
            except Exception as e:
                self.logger.warning(f"Ошибка чтения заголовка {session_path}: {e}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.warning(f"Ошибка проверки целостности {session_path}: {e}")
            return False
        
    def load_accounts(self) -> bool:
        """Загрузка всех доступных аккаунтов из папки sessions"""
        try:
            if not os.path.exists(self.sessions_dir):
                os.makedirs(self.sessions_dir)
                self.logger.info(f"Создана папка {self.sessions_dir}")
                return False
                
            session_files = [f for f in os.listdir(self.sessions_dir) if f.endswith('.session')]
            
            if not session_files:
                self.logger.info("Не найдено файлов сессий в папке sessions/")
                return False
                
            for session_file in session_files:
                account_name = session_file.replace('.session', '')
                session_path = os.path.join(self.sessions_dir, session_file)
                
                # Проверяем базовую целостность session файла
                if self._check_session_file_integrity(session_path):
                    self.accounts[account_name] = {
                        'session_path': session_path,
                        'client': None,
                        'is_active': False,
                        'last_used': None,
                        'messages_sent': 0,
                        'status': 'ready'
                    }
                else:
                    self.logger.info(f"Session файл {session_file} поврежден, пропускаем")
                
            if len(self.accounts) == 0:
                self.logger.info("Не найдено валидных session файлов")
                return False
            
            self.logger.info(f"Загружено {len(self.accounts)} аккаунтов")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка загрузки аккаунтов: {e}", exc_info=True)
            return False
    
    async def connect_account(self, account_name: str, api_id: int, api_hash: str, max_retries: int = 3) -> bool:
        """Подключение к аккаунту Telegram с retry механизмом"""
        if account_name not in self.accounts:
            self.logger.error(f"Аккаунт {account_name} не найден")
            return False
            
        session_path = self.accounts[account_name]['session_path']
        
        # Основной цикл retry для подключения аккаунта
        for attempt in range(max_retries):
            client = None
            try:
                client = TelegramClient(session_path.replace('.session', ''), api_id, api_hash)
                
                # Retry механизм для подключения к серверам Telegram
                connection_success = False
                for conn_attempt in range(3):
                    try:
                        await asyncio.wait_for(client.connect(), timeout=15.0)
                        connection_success = True
                        break
                    except asyncio.TimeoutError:
                        self.logger.warning(f"Таймаут подключения {account_name} (попытка {conn_attempt + 1}/3)")
                        if conn_attempt < 2:
                            await asyncio.sleep(2 ** conn_attempt)  # Экспоненциальная задержка
                        else:
                            raise
                    except (OSError, ConnectionError) as e:
                        self.logger.warning(f"Сетевая ошибка подключения {account_name} (попытка {conn_attempt + 1}/3): {e}")
                        if conn_attempt < 2:
                            await asyncio.sleep(2 ** conn_attempt)
                        else:
                            raise
                
                if not connection_success:
                    raise ConnectionError("Не удалось установить соединение после 3 попыток")
                
                # Проверяем авторизацию с таймаутом
                try:
                    is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=10.0)
                except asyncio.TimeoutError:
                    self.logger.warning(f"Таймаут проверки авторизации для {account_name}")
                    await client.disconnect()
                    raise
                
                if not is_authorized:
                    self.logger.warning(f"Аккаунт {account_name} не авторизован")
                    await client.disconnect()
                    return False
                
                # Успешное подключение
                self.accounts[account_name]['client'] = client
                self.accounts[account_name]['is_active'] = True
                self.accounts[account_name]['status'] = 'connected'
                
                self.logger.info(f"Аккаунт {account_name} успешно подключен (попытка {attempt + 1})")
                return True
                
            except (asyncio.TimeoutError, ConnectionError, OSError) as e:
                # Сетевые ошибки - пробуем еще раз
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass  # Игнорируем ошибки отключения
                
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.warning(f"Ошибка подключения {account_name} (попытка {attempt + 1}/{max_retries}): {e}")
                    self.logger.info(f"Повторная попытка через {wait_time} секунд...")
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.error(f"Не удалось подключить {account_name} после {max_retries} попыток: {e}")
                    return False
                    
            except (AuthKeyUnregisteredError, AuthKeyDuplicatedError, AuthKeyInvalidError) as e:
                # Поврежденная сессия - удаляем и сообщаем
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
                
                error_messages = {
                    'AuthKeyUnregisteredError': 'Сессия не зарегистрирована в Telegram',
                    'AuthKeyDuplicatedError': 'Дублированный ключ авторизации',
                    'AuthKeyInvalidError': 'Недействительный ключ авторизации'
                }
                
                reason = error_messages.get(type(e).__name__, f'Ошибка авторизации: {e}')
                self._remove_corrupted_session(account_name, session_path, reason)
                
                # Помечаем аккаунт как неактивный
                self.accounts[account_name]['is_active'] = False
                self.accounts[account_name]['status'] = 'session_corrupted'
                
                return False
                
            except Exception as e:
                # Другие критические ошибки - не повторяем
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
                
                # Проверяем на признаки поврежденной сессии в тексте ошибки
                error_str = str(e).lower()
                if any(keyword in error_str for keyword in ['session', 'auth', 'key', 'sqlite', 'database']):
                    self.logger.warning(f"Возможно поврежденная сессия {account_name}: {e}")
                    self._remove_corrupted_session(account_name, session_path, f'Подозрение на повреждение: {e}')
                    self.accounts[account_name]['is_active'] = False
                    self.accounts[account_name]['status'] = 'session_corrupted'
                    return False
                
                self.logger.error(f"Критическая ошибка подключения {account_name}: {type(e).__name__}: {e}", exc_info=True)
                return False
        
        return False
    
    async def get_next_active_account(self) -> Optional[str]:
        """Получение следующего активного аккаунта для отправки (async-safe)"""
        async with self._account_lock:  # Защита от race conditions
            active_accounts = [name for name, data in self.accounts.items() 
                              if data['is_active'] and name not in self.blocked_accounts]
            
            if not active_accounts:
                self.logger.warning("Нет доступных активных аккаунтов")
                return None
                
            # Циклическое переключение между аккаунтами
            if self.current_account_index >= len(active_accounts):
                self.current_account_index = 0
                
            account_name = active_accounts[self.current_account_index]
            self.current_account_index += 1
            
            return account_name
    
    async def mark_account_blocked(self, account_name: str, reason: str = ""):
        """Пометить аккаунт как заблокированный (async-safe)"""
        async with self._account_lock:
            if account_name in self.accounts:
                self.blocked_accounts.add(account_name)
                self.accounts[account_name]['status'] = f'blocked: {reason}'
                self.accounts[account_name]['is_active'] = False
                self.logger.warning(f"Аккаунт {account_name} помечен как заблокированный: {reason}")
    
    async def unblock_account(self, account_name: str):
        """Разблокировать аккаунт (async-safe)"""
        async with self._account_lock:
            if account_name in self.blocked_accounts:
                self.blocked_accounts.remove(account_name)
                self.accounts[account_name]['status'] = 'ready'
                self.accounts[account_name]['is_active'] = True
                self.logger.info(f"Аккаунт {account_name} разблокирован")
    
    def get_account_stats(self) -> Dict:
        """Получить статистику по всем аккаунтам"""
        stats = {
            'total_accounts': len(self.accounts),
            'active_accounts': len([a for a in self.accounts.values() if a['is_active']]),
            'blocked_accounts': len(self.blocked_accounts),
            'accounts_detail': {}
        }
        
        for name, data in self.accounts.items():
            stats['accounts_detail'][name] = {
                'status': data['status'],
                'messages_sent': data['messages_sent'],
                'last_used': data['last_used'],
                'is_blocked': name in self.blocked_accounts
            }
            
        return stats
    
    def print_account_stats_russian(self):
        """Красивый вывод статистики аккаунтов на русском языке"""
        stats = self.get_account_stats()
        
        print("\n" + "="*60)
        print("📊 СТАТИСТИКА АККАУНТОВ")
        print("="*60)
        
        # Общая статистика
        print(f"📱 Всего аккаунтов: {stats['total_accounts']}")
        print(f"✅ Активных: {stats['active_accounts']}")
        print(f"❌ Заблокированных: {stats['blocked_accounts']}")
        print(f"⚠️ Неактивных: {stats['total_accounts'] - stats['active_accounts']}")
        
        print("\n" + "-"*60)
        print("📋 ДЕТАЛЬНАЯ ИНФОРМАЦИЯ ПО АККАУНТАМ:")
        print("-"*60)
        
        if not stats['accounts_detail']:
            print("📭 Нет загруженных аккаунтов")
            return
        
        # Детальная информация по каждому аккаунту
        for account_name, details in stats['accounts_detail'].items():
            # Определяем иконку статуса
            if details['is_blocked']:
                status_icon = "🚫"
                status_text = "ЗАБЛОКИРОВАН"
            elif details['status'] == 'connected':
                status_icon = "✅"
                status_text = "ПОДКЛЮЧЕН"
            elif details['status'] == 'disconnected':
                status_icon = "⚠️"
                status_text = "ОТКЛЮЧЕН"
            elif details['status'] == 'reconnecting':
                status_icon = "🔄"
                status_text = "ПЕРЕПОДКЛЮЧЕНИЕ"
            else:
                status_icon = "❓"
                status_text = details['status'].upper()
            
            print(f"\n{status_icon} {account_name}")
            print(f"   � НСтатус: {status_text}(Если уже был подключен через кнопку 3 можно переподключить все отключенные аккаунты)")
            print(f"   �  Отправлено сообщений: {details['messages_sent']}")
            
            # Форматируем время последнего использования
            if details['last_used'] and details['last_used'] > 0:
                try:
                    from datetime import datetime
                    if isinstance(details['last_used'], (int, float)):
                        # Проверяем что timestamp разумный (после 2020 года)
                        if details['last_used'] > 1577836800:  # 1 января 2020
                            last_used_dt = datetime.fromtimestamp(details['last_used'])
                            last_used_str = last_used_dt.strftime("%d.%m.%Y %H:%M:%S")
                        else:
                            last_used_str = "Некорректное время"
                    else:
                        last_used_str = str(details['last_used'])
                    print(f"   ⏰ Последнее использование: {last_used_str}")
                except Exception as e:
                    print(f"   ⏰ Последнее использование: Ошибка форматирования ({details['last_used']})")
            else:
                print(f"   ⏰ Последнее использование: Никогда")
        
        print("\n" + "="*60)
    
    def update_message_sent(self, account_name: str):
        """Обновить счетчик отправленных сообщений для аккаунта"""
        if account_name in self.accounts:
            import time
            self.accounts[account_name]['messages_sent'] += 1
            self.accounts[account_name]['last_used'] = time.time()  # Используем реальный unix timestamp
            self.logger.debug(f"Обновлена статистика для {account_name}: {self.accounts[account_name]['messages_sent']} сообщений")
    
    async def reconnect_account(self, account_name: str, api_id: int, api_hash: str) -> bool:
        """Переподключение аккаунта (отключение + подключение)"""
        if account_name not in self.accounts:
            self.logger.error(f"Аккаунт {account_name} не найден для переподключения")
            return False
        
        # Сначала отключаем
        account_data = self.accounts[account_name]
        if account_data['client']:
            try:
                # Корректное отключение с ожиданием завершения задач
                if account_data['client'].is_connected():
                    await asyncio.wait_for(account_data['client'].disconnect(), timeout=5.0)
                    # Даем время на завершение всех фоновых задач
                    await asyncio.sleep(0.5)
                self.logger.info(f"Аккаунт {account_name} отключен для переподключения")
            except asyncio.TimeoutError:
                self.logger.warning(f"Таймаут при отключении {account_name}")
            except Exception as e:
                self.logger.warning(f"Ошибка при отключении {account_name}: {e}")
        
        # Сбрасываем состояние
        account_data['client'] = None
        account_data['is_active'] = False
        account_data['status'] = 'reconnecting'
        
        # Даем время на очистку ресурсов
        await asyncio.sleep(0.3)
        
        # Подключаем заново
        return await self.connect_account(account_name, api_id, api_hash)
    
    async def disconnect_all(self):
        """Отключить все аккаунты"""
        disconnect_tasks = []
        
        for account_name, data in self.accounts.items():
            if data['client'] and data['is_active']:
                disconnect_tasks.append(self._disconnect_single_account(account_name, data))
        
        if disconnect_tasks:
            # Отключаем все аккаунты параллельно
            results = await asyncio.gather(*disconnect_tasks, return_exceptions=True)
            
            # Логируем результаты
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    account_name = list(self.accounts.keys())[i]
                    self.logger.error(f"Ошибка отключения {account_name}: {result}")
            
            # Даем дополнительное время на завершение всех фоновых задач
            await asyncio.sleep(1.0)
    
    async def _disconnect_single_account(self, account_name: str, account_data: dict):
        """Отключение одного аккаунта с таймаутом"""
        try:
            if account_data['client'].is_connected():
                await asyncio.wait_for(account_data['client'].disconnect(), timeout=10.0)
                # Даем время на завершение фоновых задач
                await asyncio.sleep(0.3)
            account_data['is_active'] = False
            account_data['status'] = 'disconnected'
            self.logger.info(f"Аккаунт {account_name} отключен")
        except asyncio.TimeoutError:
            self.logger.warning(f"Таймаут отключения {account_name}")
            account_data['is_active'] = False
            account_data['status'] = 'timeout_disconnect'
        except Exception as e:
            self.logger.error(f"Ошибка отключения {account_name}: {e}")
            account_data['is_active'] = False
            account_data['status'] = 'error_disconnect'
    
    async def check_connections_health(self) -> Dict[str, bool]:
        """Проверка здоровья всех подключений"""
        health_status = {}
        
        for account_name, data in self.accounts.items():
            if not data['is_active'] or not data['client']:
                health_status[account_name] = False
                continue
            
            try:
                # Быстрая проверка подключения
                await asyncio.wait_for(data['client'].get_me(), timeout=5.0)
                health_status[account_name] = True
            except Exception as e:
                self.logger.warning(f"Проблема с подключением {account_name}: {e}")
                health_status[account_name] = False
                # Помечаем как неактивный для переподключения
                data['is_active'] = False
                data['status'] = 'connection_lost'
        
        return health_status
    
    async def get_active_accounts_list(self) -> List[str]:
        """Получить список активных аккаунтов (async-safe)"""
        async with self._account_lock:
            return [name for name, data in self.accounts.items() 
                   if data['is_active'] and name not in self.blocked_accounts]
    
    async def auto_reconnect_failed(self, api_id: int, api_hash: str) -> int:
        """Автоматическое переподключение неудачных аккаунтов"""
        health_status = await self.check_connections_health()
        reconnected = 0
        failed_accounts = [name for name, is_healthy in health_status.items() 
                          if not is_healthy and name not in self.blocked_accounts]
        
        if not failed_accounts:
            return 0
        
        print(f"🔄 Переподключение {len(failed_accounts)} аккаунтов...")
        
        for i, account_name in enumerate(failed_accounts, 1):
            print(f"   [{i}/{len(failed_accounts)}] Переподключение {account_name}...", end=" ")
            self.logger.info(f"Попытка переподключения {account_name}...")
            
            if await self.reconnect_account(account_name, api_id, api_hash):
                reconnected += 1
                print("✅")
                self.logger.info(f"{account_name} успешно переподключен")
            else:
                print("❌")
                self.logger.warning(f"Не удалось переподключить {account_name}")
        
        return reconnected
    
    def add_new_account(self, session_file_path: str, account_name: str) -> bool:
        """Добавить новый аккаунт на ходу"""
        try:
            new_session_path = os.path.join(self.sessions_dir, f"{account_name}.session")
            
            # Копируем файл сессии
            import shutil
            shutil.copy2(session_file_path, new_session_path)
            
            self.accounts[account_name] = {
                'session_path': new_session_path,
                'client': None,
                'is_active': False,
                'last_used': None,
                'messages_sent': 0,
                'status': 'ready'
            }
            
            self.logger.info(f"Добавлен новый аккаунт: {account_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка добавления аккаунта {account_name}: {e}", exc_info=True)
            return False
    
    def cleanup_corrupted_sessions(self) -> int:
        """Очистка всех поврежденных session файлов"""
        cleaned_count = 0
        
        try:
            if not os.path.exists(self.sessions_dir):
                return 0
            
            session_files = [f for f in os.listdir(self.sessions_dir) if f.endswith('.session')]
            
            for session_file in session_files:
                session_path = os.path.join(self.sessions_dir, session_file)
                account_name = session_file.replace('.session', '')
                
                if not self._check_session_file_integrity(session_path):
                    if self._remove_corrupted_session(account_name, session_path, "Не прошел проверку целостности"):
                        cleaned_count += 1
            
            if cleaned_count > 0:
                self.logger.info(f"Очищено {cleaned_count} поврежденных session файлов")
            
            return cleaned_count
            
        except Exception as e:
            self.logger.error(f"Ошибка очистки поврежденных сессий: {e}")
            return 0