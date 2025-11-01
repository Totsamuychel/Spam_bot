import os
import json
import logging
import asyncio
from typing import List, Dict, Optional
from threading import Lock
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

class AccountManager:
    """Управление аккаунтами Telegram для мультиаккаунтной рассылки"""
    
    def __init__(self, sessions_dir: str = "sessions"):
        self.sessions_dir = sessions_dir
        self.accounts = {}
        self.blocked_accounts = set()
        self.current_account_index = 0
        self._account_lock = Lock()  # Защита от race conditions
        self.logger = logging.getLogger(__name__)
        
    def load_accounts(self) -> bool:
        """Загрузка всех доступных аккаунтов из папки sessions"""
        try:
            if not os.path.exists(self.sessions_dir):
                os.makedirs(self.sessions_dir)
                self.logger.warning(f"Создана папка {self.sessions_dir}. Добавьте файлы сессий.")
                return False
                
            session_files = [f for f in os.listdir(self.sessions_dir) if f.endswith('.session')]
            
            if not session_files:
                self.logger.warning("Не найдено файлов сессий в папке sessions/")
                return False
                
            for session_file in session_files:
                account_name = session_file.replace('.session', '')
                session_path = os.path.join(self.sessions_dir, session_file)
                
                self.accounts[account_name] = {
                    'session_path': session_path,
                    'client': None,
                    'is_active': False,
                    'last_used': None,
                    'messages_sent': 0,
                    'status': 'ready'
                }
                
            self.logger.info(f"Загружено {len(self.accounts)} аккаунтов")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка загрузки аккаунтов: {e}")
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
                    
            except Exception as e:
                # Критические ошибки - не повторяем
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
                
                self.logger.error(f"Критическая ошибка подключения {account_name}: {type(e).__name__}: {e}")
                return False
        
        return False
    
    def get_next_active_account(self) -> Optional[str]:
        """Получение следующего активного аккаунта для отправки (thread-safe)"""
        with self._account_lock:  # Защита от race conditions
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
    
    def mark_account_blocked(self, account_name: str, reason: str = ""):
        """Пометить аккаунт как заблокированный (thread-safe)"""
        with self._account_lock:
            if account_name in self.accounts:
                self.blocked_accounts.add(account_name)
                self.accounts[account_name]['status'] = f'blocked: {reason}'
                self.accounts[account_name]['is_active'] = False
                self.logger.warning(f"Аккаунт {account_name} помечен как заблокированный: {reason}")
    
    def unblock_account(self, account_name: str):
        """Разблокировать аккаунт (thread-safe)"""
        with self._account_lock:
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
    
    async def reconnect_account(self, account_name: str, api_id: int, api_hash: str) -> bool:
        """Переподключение аккаунта (отключение + подключение)"""
        if account_name not in self.accounts:
            self.logger.error(f"Аккаунт {account_name} не найден для переподключения")
            return False
        
        # Сначала отключаем
        account_data = self.accounts[account_name]
        if account_data['client'] and account_data['is_active']:
            try:
                await account_data['client'].disconnect()
                self.logger.info(f"Аккаунт {account_name} отключен для переподключения")
            except Exception as e:
                self.logger.warning(f"Ошибка при отключении {account_name}: {e}")
        
        # Сбрасываем состояние
        account_data['client'] = None
        account_data['is_active'] = False
        account_data['status'] = 'reconnecting'
        
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
    
    async def _disconnect_single_account(self, account_name: str, account_data: dict):
        """Отключение одного аккаунта с таймаутом"""
        try:
            await asyncio.wait_for(account_data['client'].disconnect(), timeout=10.0)
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
    
    async def auto_reconnect_failed(self, api_id: int, api_hash: str) -> int:
        """Автоматическое переподключение неудачных аккаунтов"""
        health_status = await self.check_connections_health()
        reconnected = 0
        
        for account_name, is_healthy in health_status.items():
            if not is_healthy and account_name not in self.blocked_accounts:
                self.logger.info(f"Попытка переподключения {account_name}...")
                if await self.reconnect_account(account_name, api_id, api_hash):
                    reconnected += 1
                    self.logger.info(f"✅ {account_name} успешно переподключен")
                else:
                    self.logger.warning(f"❌ Не удалось переподключить {account_name}")
        
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
            self.logger.error(f"Ошибка добавления аккаунта {account_name}: {e}")
            return False