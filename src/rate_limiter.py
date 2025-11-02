import time
import asyncio
import logging
from typing import Dict, List
from collections import defaultdict, deque

class RateLimiter:
    """Контроль лимитов отправки сообщений для защиты от блокировок"""
    
    def __init__(self):
        # Лимиты Telegram (консервативные значения для безопасности)
        self.MESSAGES_PER_MINUTE = 6  # Сообщений в минуту
        self.MESSAGES_PER_HOUR = 36  # Сообщений в час
        self.NEW_CHATS_PER_DAY = 12    # Новых чатов в день
        
        # Отслеживание отправленных сообщений по аккаунтам с ограничением размера
        # Максимум 300 записей на аккаунт (достаточно для отслеживания часа работы)
        self.message_history = defaultdict(lambda: deque(maxlen=300))
        self.new_chats_history = defaultdict(lambda: deque(maxlen=100))  # Максимум 100 новых чатов
        self.account_penalties = defaultdict(int)  # Штрафы за блокировки
        
        self.logger = logging.getLogger(__name__)
        
    def can_send_message(self, account_name: str, is_new_chat: bool = False) -> tuple[bool, float]:
        """
        Проверить, можно ли отправить сообщение
        Возвращает (можно_отправить, время_ожидания)
        """
        current_time = time.time()
        
        # Очистка старых записей
        self._cleanup_old_records(account_name, current_time)
        
        # Проверка лимита сообщений в минуту
        minute_messages = len([t for t in self.message_history[account_name] 
                              if current_time - t < 60])
        
        if minute_messages >= self.MESSAGES_PER_MINUTE:
            wait_time = 60 - (current_time - min(self.message_history[account_name]))
            self.logger.warning(f"Лимит в минуту для {account_name}. Ожидание: {wait_time:.1f}с")
            return False, wait_time
        
        # Проверка лимита сообщений в час
        hour_messages = len([t for t in self.message_history[account_name] 
                            if current_time - t < 3600])
        
        if hour_messages >= self.MESSAGES_PER_HOUR:
            wait_time = 3600 - (current_time - min(self.message_history[account_name]))
            self.logger.warning(f"Лимит в час для {account_name}. Ожидание: {wait_time:.1f}с")
            return False, wait_time
        
        # Проверка лимита новых чатов в день
        if is_new_chat:
            day_new_chats = len([t for t in self.new_chats_history[account_name] 
                               if current_time - t < 86400])
            
            if day_new_chats >= self.NEW_CHATS_PER_DAY:
                wait_time = 86400 - (current_time - min(self.new_chats_history[account_name]))
                self.logger.warning(f"Лимит новых чатов для {account_name}. Ожидание: {wait_time:.1f}с")
                return False, wait_time
        
        # Учет штрафов за предыдущие блокировки
        penalty_delay = self.account_penalties[account_name] * 2  # 2 секунды за каждую блокировку
        
        return True, penalty_delay
    
    def record_message_sent(self, account_name: str, is_new_chat: bool = False):
        """Записать отправленное сообщение"""
        current_time = time.time()
        self.message_history[account_name].append(current_time)
        
        if is_new_chat:
            self.new_chats_history[account_name].append(current_time)
        
        self.logger.debug(f"Записано сообщение для {account_name}")
    
    def record_account_blocked(self, account_name: str, block_type: str = "unknown"):
        """Записать блокировку аккаунта"""
        self.account_penalties[account_name] += 1
        self.logger.warning(f"Блокировка {account_name} ({block_type}). Штрафов: {self.account_penalties[account_name]}")
    
    def _cleanup_old_records(self, account_name: str, current_time: float):
        """Очистка старых записей для экономии памяти"""
        # Очистка истории сообщений (оставляем только за последний час)
        hour_cutoff = current_time - 3600
        message_deque = self.message_history[account_name]
        
        # Более эффективная очистка - удаляем старые записи пачками
        while message_deque and message_deque[0] < hour_cutoff:
            message_deque.popleft()
        
        # Очистка истории новых чатов (оставляем только за последние сутки)
        day_cutoff = current_time - 86400
        chats_deque = self.new_chats_history[account_name]
        
        while chats_deque and chats_deque[0] < day_cutoff:
            chats_deque.popleft()
        
        # Дополнительная защита от переполнения памяти
        # Если deque все еще слишком большой, оставляем только последние записи
        if len(message_deque) > 250:
            # Оставляем только последние 200 записей
            new_deque = deque(list(message_deque)[-200:], maxlen=300)
            self.message_history[account_name] = new_deque
            
        if len(chats_deque) > 80:
            # Оставляем только последние 60 записей
            new_deque = deque(list(chats_deque)[-60:], maxlen=100)
            self.new_chats_history[account_name] = new_deque
    
    async def smart_delay(self, account_name: str, base_delay: float = 1.0):
        """Умная задержка с учетом нагрузки аккаунта"""
        import random
        
        # Базовая задержка с увеличенным диапазоном случайности
        delay = random.uniform(3.0, 6.0)
        
        # Увеличиваем задержку в зависимости от количества штрафов
        penalty_multiplier = 1 + (self.account_penalties[account_name] * 0.5)
        delay *= penalty_multiplier
        
        self.logger.debug(f"Задержка для {account_name}: {delay:.1f}с (штраф: x{penalty_multiplier:.1f})")
        await asyncio.sleep(delay)
    
    async def non_critical_delay(self):
        """Задержка для некритических операций"""
        import random
        delay = random.uniform(0.2, 1.0)
        await asyncio.sleep(delay)
    
    def get_account_limits_info(self, account_name: str) -> Dict:
        """Получить информацию о текущих лимитах аккаунта"""
        current_time = time.time()
        self._cleanup_old_records(account_name, current_time)
        
        minute_messages = len([t for t in self.message_history[account_name] 
                              if current_time - t < 60])
        hour_messages = len([t for t in self.message_history[account_name] 
                            if current_time - t < 3600])
        day_new_chats = len([t for t in self.new_chats_history[account_name] 
                           if current_time - t < 86400])
        
        return {
            'messages_per_minute': f"{minute_messages}/{self.MESSAGES_PER_MINUTE}",
            'messages_per_hour': f"{hour_messages}/{self.MESSAGES_PER_HOUR}",
            'new_chats_per_day': f"{day_new_chats}/{self.NEW_CHATS_PER_DAY}",
            'penalties': self.account_penalties[account_name],
            'can_send_now': self.can_send_message(account_name)[0]
        }
    
    def get_account_limits_info_russian(self, account_name: str) -> Dict:
        """Получить информацию о текущих лимитах аккаунта на русском языке"""
        current_time = time.time()
        self._cleanup_old_records(account_name, current_time)
        
        minute_messages = len([t for t in self.message_history[account_name] 
                              if current_time - t < 60])
        hour_messages = len([t for t in self.message_history[account_name] 
                            if current_time - t < 3600])
        day_new_chats = len([t for t in self.new_chats_history[account_name] 
                           if current_time - t < 86400])
        
        penalties = self.account_penalties[account_name]
        can_send = self.can_send_message(account_name)[0]
        
        return {
            'Сообщений в минуту': f"{minute_messages}/{self.MESSAGES_PER_MINUTE}",
            'Сообщений в час': f"{hour_messages}/{self.MESSAGES_PER_HOUR}",
            'Новых чатов в день': f"{day_new_chats}/{self.NEW_CHATS_PER_DAY}",
            'Штрафы': penalties,
            'Может отправлять сейчас': "✅ Да" if can_send else "❌ Нет"
        }
    
    def reset_account_penalties(self, account_name: str):
        """Сбросить штрафы аккаунта"""
        self.account_penalties[account_name] = 0
        self.logger.info(f"Штрафы для {account_name} сброшены")
    
    def get_optimal_delay(self, total_messages: int, available_accounts: int) -> float:
        """Рассчитать оптимальную задержку для равномерного распределения"""
        if available_accounts == 0:
            return 60.0  # Если нет аккаунтов, большая задержка
        
        # Рассчитываем задержку чтобы не превысить лимиты
        messages_per_account = total_messages / available_accounts
        
        if messages_per_account <= self.MESSAGES_PER_MINUTE:
            return 3.0  # Минимальная задержка
        elif messages_per_account <= self.MESSAGES_PER_HOUR:
            return 60.0 / self.MESSAGES_PER_MINUTE  # Равномерно в течение часа
        else:
            return 3600.0 / self.MESSAGES_PER_HOUR  # Равномерно с максимальной скоростью
    
    def cleanup_all_accounts(self):
        """Полная очистка истории всех аккаунтов для освобождения памяти"""
        current_time = time.time()
        
        for account_name in list(self.message_history.keys()):
            self._cleanup_old_records(account_name, current_time)
        
        # Удаляем пустые записи
        empty_accounts = [name for name, deque_obj in self.message_history.items() if not deque_obj]
        for account_name in empty_accounts:
            del self.message_history[account_name]
            
        empty_chats = [name for name, deque_obj in self.new_chats_history.items() if not deque_obj]
        for account_name in empty_chats:
            del self.new_chats_history[account_name]
            
        self.logger.info(f"Очищена история для {len(empty_accounts)} аккаунтов")
    
    def get_memory_usage_info(self) -> Dict:
        """Получить информацию об использовании памяти"""
        total_message_records = sum(len(deque_obj) for deque_obj in self.message_history.values())
        total_chat_records = sum(len(deque_obj) for deque_obj in self.new_chats_history.values())
        
        return {
            'accounts_tracked': len(self.message_history),
            'total_message_records': total_message_records,
            'total_chat_records': total_chat_records,
            'memory_usage_estimate_mb': (total_message_records + total_chat_records) * 0.001,  # Примерная оценка
            'accounts_with_penalties': len([p for p in self.account_penalties.values() if p > 0])
        }
    
    def force_cleanup_account(self, account_name: str):
        """Принудительная очистка истории конкретного аккаунта"""
        if account_name in self.message_history:
            self.message_history[account_name].clear()
        if account_name in self.new_chats_history:
            self.new_chats_history[account_name].clear()
        if account_name in self.account_penalties:
            self.account_penalties[account_name] = 0
        
        self.logger.info(f"Принудительно очищена история аккаунта {account_name}")