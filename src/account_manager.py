import os
import json
import logging
from typing import List, Dict, Optional
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

class AccountManager:
    """Управление аккаунтами Telegram для мультиаккаунтной рассылки"""
    
    def __init__(self, sessions_dir: str = "sessions"):
        self.sessions_dir = sessions_dir
        self.accounts = {}
        self.blocked_accounts = set()
        self.current_account_index = 0
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
    
    async def connect_account(self, account_name: str, api_id: int, api_hash: str) -> bool:
        """Подключение к аккаунту Telegram"""
        try:
            if account_name not in self.accounts:
                self.logger.error(f"Аккаунт {account_name} не найден")
                return False
                
            session_path = self.accounts[account_name]['session_path']
            client = TelegramClient(session_path.replace('.session', ''), api_id, api_hash)
            
            await client.connect()
            
            if not await client.is_user_authorized():
                self.logger.warning(f"Аккаунт {account_name} не авторизован")
                await client.disconnect()
                return False
                
            self.accounts[account_name]['client'] = client
            self.accounts[account_name]['is_active'] = True
            self.accounts[account_name]['status'] = 'connected'
            
            self.logger.info(f"Аккаунт {account_name} успешно подключен")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка подключения аккаунта {account_name}: {e}")
            return False
    
    def get_next_active_account(self) -> Optional[str]:
        """Получение следующего активного аккаунта для отправки"""
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
        """Пометить аккаунт как заблокированный"""
        if account_name in self.accounts:
            self.blocked_accounts.add(account_name)
            self.accounts[account_name]['status'] = f'blocked: {reason}'
            self.logger.warning(f"Аккаунт {account_name} помечен как заблокированный: {reason}")
    
    def unblock_account(self, account_name: str):
        """Разблокировать аккаунт"""
        if account_name in self.blocked_accounts:
            self.blocked_accounts.remove(account_name)
            self.accounts[account_name]['status'] = 'ready'
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
    
    async def disconnect_all(self):
        """Отключить все аккаунты"""
        for account_name, data in self.accounts.items():
            if data['client'] and data['is_active']:
                try:
                    await data['client'].disconnect()
                    data['is_active'] = False
                    self.logger.info(f"Аккаунт {account_name} отключен")
                except Exception as e:
                    self.logger.error(f"Ошибка отключения {account_name}: {e}")
    
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