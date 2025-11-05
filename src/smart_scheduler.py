#!/usr/bin/env python3
"""
Умный планировщик рассылки
Распределяет нагрузку между аккаунтами для максимальной безопасности
"""

import asyncio
import random
import time
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

@dataclass
class AccountSchedule:
    """Расписание для одного аккаунта"""
    account_name: str
    next_send_time: float
    messages_sent_today: int
    new_chats_today: int
    last_activity: float
    is_active: bool = True
    penalty_multiplier: float = 1.0

class SmartScheduler:
    """Умный планировщик для безопасной рассылки"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Защита от race conditions
        self._scheduler_lock = asyncio.Lock()
        
        # Расписания аккаунтов
        self.account_schedules: Dict[str, AccountSchedule] = {}
        
        # Настройки безопасности
        self.MIN_DELAY_BETWEEN_ACCOUNTS = 30.0  # Минимум 30 сек между аккаунтами
        self.MAX_CONCURRENT_ACCOUNTS = 3        # Максимум 3 аккаунта одновременно
        self.DAILY_RESET_HOUR = 0              # Час сброса дневных лимитов
        
        # Временные окна активности (имитация человеческого поведения)
        self.ACTIVE_HOURS = {
            'morning': (8, 12),    # Утро
            'afternoon': (13, 17), # День  
            'evening': (18, 22)    # Вечер
        }
        
        # Последний сброс дневных счетчиков
        self.last_daily_reset = time.time()
        
    async def add_account(self, account_name: str):
        """Добавить аккаунт в планировщик"""
        async with self._scheduler_lock:
            if account_name not in self.account_schedules:
                self.account_schedules[account_name] = AccountSchedule(
                    account_name=account_name,
                    next_send_time=time.time() + random.uniform(10, 60),  # Случайная начальная задержка
                    messages_sent_today=0,
                    new_chats_today=0,
                    last_activity=0,
                    is_active=True
                )
                self.logger.info(f"Аккаунт {account_name} добавлен в планировщик")
    
    async def remove_account(self, account_name: str):
        """Удалить аккаунт из планировщика"""
        async with self._scheduler_lock:
            if account_name in self.account_schedules:
                del self.account_schedules[account_name]
                self.logger.info(f"Аккаунт {account_name} удален из планировщика")
    
    async def deactivate_account(self, account_name: str, reason: str = ""):
        """Деактивировать аккаунт"""
        async with self._scheduler_lock:
            if account_name in self.account_schedules:
                self.account_schedules[account_name].is_active = False
                self.logger.warning(f"Аккаунт {account_name} деактивирован: {reason}")
    
    async def get_next_available_account(self, is_new_chat: bool = False) -> Optional[str]:
        """Получить следующий доступный аккаунт с учетом расписания"""
        async with self._scheduler_lock:
            current_time = time.time()
            
            # Проверяем нужно ли сбросить дневные счетчики
            self._check_daily_reset(current_time)
            
            # Фильтруем доступные аккаунты
            available_accounts = []
            
            for account_name, schedule in self.account_schedules.items():
                if not schedule.is_active:
                    continue
                    
                # Проверяем временные ограничения
                if current_time < schedule.next_send_time:
                    continue
                
                # Проверяем дневные лимиты
                if schedule.messages_sent_today >= 36:  # Дневной лимит сообщений
                    continue
                    
                if is_new_chat and schedule.new_chats_today >= 12:  # Лимит новых чатов
                    continue
            
                # Проверяем активные часы (имитация человеческого поведения)
                # Убрана блокировка в неактивные часы - рассылка работает всегда
                
                available_accounts.append((account_name, schedule))
            
            if not available_accounts:
                return None
            
            # Выбираем аккаунт с наименьшей недавней активностью
            available_accounts.sort(key=lambda x: x[1].last_activity)
            
            # Добавляем элемент случайности среди топ-3 наименее активных
            top_accounts = available_accounts[:min(3, len(available_accounts))]
            selected_account, selected_schedule = random.choice(top_accounts)
            
            return selected_account
    
    async def schedule_next_send(self, account_name: str, is_new_chat: bool = False):
        """Запланировать следующую отправку для аккаунта"""
        async with self._scheduler_lock:
            if account_name not in self.account_schedules:
                await self.add_account(account_name)
            
            schedule = self.account_schedules[account_name]
        current_time = time.time()
        
        # Обновляем статистику
        schedule.messages_sent_today += 1
        if is_new_chat:
            schedule.new_chats_today += 1
        schedule.last_activity = current_time
        
        # Рассчитываем следующее время отправки
        # Базовая задержка 3-6 секунд (это единственная задержка!)
        total_delay = random.uniform(3.0, 6.0)
        
        # Применяем штрафной множитель только если есть штрафы
        if schedule.penalty_multiplier > 1.0:
            total_delay *= schedule.penalty_multiplier
        
        schedule.next_send_time = current_time + total_delay
        
        self.logger.debug(f"Следующая отправка для {account_name} через {total_delay:.1f}с")
    
    async def apply_penalty(self, account_name: str, penalty_type: str):
        """Применить штраф к аккаунту"""
        async with self._scheduler_lock:
            schedule = self.account_schedules.get(account_name)
            if schedule is None:
                self.logger.warning(f"Попытка применить штраф к несуществующему аккаунту: {account_name}")
                return
        
        if penalty_type == "flood_wait":
            schedule.penalty_multiplier += 0.5
            schedule.next_send_time = time.time() + random.uniform(300, 600)  # 5-10 минут
            
        elif penalty_type == "peer_flood":
            schedule.is_active = False
            self.logger.error(f"Аккаунт {account_name} деактивирован из-за PeerFlood")
            
        elif penalty_type == "critical_error":
            schedule.is_active = False
            self.logger.error(f"Аккаунт {account_name} деактивирован из-за критической ошибки")
        
        self.logger.warning(f"Штраф применен к {account_name}: {penalty_type}")
    
    def get_account_load_balance(self) -> Dict[str, float]:
        """Получить баланс нагрузки между аккаунтами"""
        if not self.account_schedules:
            return {}
        
        current_time = time.time()
        load_balance = {}
        
        for account_name, schedule in self.account_schedules.items():
            if not schedule.is_active:
                load_balance[account_name] = 0.0
                continue
            
            # Рассчитываем нагрузку как процент от дневного лимита
            message_load = schedule.messages_sent_today / 36.0
            chat_load = schedule.new_chats_today / 12.0
            
            # Учитываем время до следующей отправки
            time_factor = max(0, (schedule.next_send_time - current_time) / 3600.0)  # В часах
            
            total_load = (message_load + chat_load + time_factor) / 3.0
            load_balance[account_name] = min(1.0, total_load)
        
        return load_balance
    
    def _check_daily_reset(self, current_time: float):
        """Проверить нужно ли сбросить дневные счетчики"""
        # Получаем текущую дату
        current_date = time.localtime(current_time).tm_yday  # День года
        last_reset_date = time.localtime(self.last_daily_reset).tm_yday
        
        # Сбрасываем если сменился день
        if current_date != last_reset_date:
            for schedule in self.account_schedules.values():
                schedule.messages_sent_today = 0
                schedule.new_chats_today = 0
                schedule.penalty_multiplier = max(1.0, schedule.penalty_multiplier * 0.8)  # Уменьшаем штрафы
            
            self.last_daily_reset = current_time
            self.logger.info(f"Дневные счетчики сброшены (новый день: {current_date})")
    
    def _is_active_hour(self, current_time: float) -> bool:
        """Проверить активные часы для имитации человеческого поведения"""
        current_hour = time.localtime(current_time).tm_hour
        
        for period, (start, end) in self.ACTIVE_HOURS.items():
            if start <= current_hour <= end:
                return True
        
        return False
    
    def get_scheduler_stats(self) -> Dict:
        """Получить статистику планировщика"""
        current_time = time.time()
        active_accounts = sum(1 for s in self.account_schedules.values() if s.is_active)
        
        total_messages_today = sum(s.messages_sent_today for s in self.account_schedules.values())
        total_chats_today = sum(s.new_chats_today for s in self.account_schedules.values())
        
        # Средняя нагрузка
        load_balance = self.get_account_load_balance()
        avg_load = sum(load_balance.values()) / len(load_balance) if load_balance else 0
        
        return {
            'total_accounts': len(self.account_schedules),
            'active_accounts': active_accounts,
            'messages_sent_today': total_messages_today,
            'new_chats_today': total_chats_today,
            'average_load': avg_load,
            'is_active_hour': self._is_active_hour(current_time)
        }
    
    def optimize_schedule(self):
        """Оптимизировать расписание для равномерного распределения"""
        current_time = time.time()
        active_schedules = [s for s in self.account_schedules.values() if s.is_active]
        
        if len(active_schedules) < 2:
            return
        
        # Сортируем по времени следующей отправки
        active_schedules.sort(key=lambda s: s.next_send_time)
        
        # Перераспределяем время для избежания одновременных отправок
        for i, schedule in enumerate(active_schedules):
            if i > 0:
                prev_time = active_schedules[i-1].next_send_time
                min_gap = self.MIN_DELAY_BETWEEN_ACCOUNTS
                
                if schedule.next_send_time - prev_time < min_gap:
                    schedule.next_send_time = prev_time + min_gap + random.uniform(5, 15)
        
        self.logger.debug("Расписание оптимизировано для равномерного распределения")

# Глобальный экземпляр планировщика
smart_scheduler = SmartScheduler()