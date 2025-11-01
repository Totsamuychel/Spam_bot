import json
import logging
import queue
from typing import List, Dict, Optional
from dataclasses import dataclass
from queue import Queue
import random

@dataclass
class MessageTask:
    """Задача отправки сообщения"""
    recipient_id: Optional[int]
    recipient_username: Optional[str] 
    recipient_phone: Optional[str]
    message_text: str
    account_name: str
    priority: int = 1
    is_new_chat: bool = True
    retry_count: int = 0

class MessageQueue:
    """Управление очередью сообщений и распределение по аккаунтам"""
    
    def __init__(self, data_file: str = "data/messages_data.json"):
        self.data_file = data_file
        self.message_queue = Queue()
        self.failed_messages = []
        self.completed_messages = []
        self.logger = logging.getLogger(__name__)
        
    def load_messages_data(self) -> bool:
        """Загрузка данных сообщений из JSON файла"""
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.message_text = data.get('message', '')
            self.recipients = data.get('recipients', [])
            
            if not self.message_text:
                self.logger.error("Не найден текст сообщения в файле данных")
                return False
                
            if not self.recipients:
                self.logger.error("Не найдены получатели в файле данных")
                return False
                
            self.logger.info(f"Загружено {len(self.recipients)} получателей")
            return True
            
        except FileNotFoundError:
            self.logger.error(f"Файл {self.data_file} не найден")
            return False
        except json.JSONDecodeError as e:
            self.logger.error(f"Ошибка парсинга JSON: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Ошибка загрузки данных: {e}")
            return False
    
    def create_message_queue(self, available_accounts: List[str], max_messages: Optional[int] = None) -> int:
        """
        Создание очереди сообщений с распределением по аккаунтам
        Возвращает количество созданных задач
        """
        if not available_accounts:
            self.logger.error("Нет доступных аккаунтов для создания очереди")
            return 0
            
        if not hasattr(self, 'recipients'):
            self.logger.error("Данные получателей не загружены")
            return 0
        
        # Ограничиваем количество сообщений если указано
        recipients_to_process = self.recipients[:max_messages] if max_messages else self.recipients
        
        # Перемешиваем получателей для равномерного распределения
        recipients_shuffled = recipients_to_process.copy()
        random.shuffle(recipients_shuffled)
        
        # Распределяем получателей по аккаунтам
        account_index = 0
        tasks_created = 0
        
        for recipient in recipients_shuffled:
            # Выбираем аккаунт циклически
            account_name = available_accounts[account_index % len(available_accounts)]
            account_index += 1
            
            # Создаем задачу отправки
            task = self._create_message_task(recipient, account_name)
            if task:
                try:
                    self.message_queue.put(task)
                    tasks_created += 1
                except Exception as e:
                    self.logger.error(f"Ошибка при добавлении задачи в очередь: {type(e).__name__}: {e}")
                    # Добавляем в список неудачных если не можем поставить в очередь
                    self.failed_messages.append(task)
        
        self.logger.info(f"Создано {tasks_created} задач в очереди")
        return tasks_created
    
    def _create_message_task(self, recipient: Dict, account_name: str) -> Optional[MessageTask]:
        """Создание задачи отправки сообщения"""
        try:
            # Определяем тип получателя
            recipient_id = recipient.get('user_id')
            recipient_username = recipient.get('username')
            recipient_phone = recipient.get('phone')
            
            if not any([recipient_id, recipient_username, recipient_phone]):
                self.logger.warning(f"Некорректный получатель: {recipient}")
                return None
            
            # Определяем приоритет (новые чаты имеют меньший приоритет)
            priority = 2 if recipient_id else 1  # ID имеет больший приоритет чем username/phone
            
            task = MessageTask(
                recipient_id=recipient_id,
                recipient_username=recipient_username,
                recipient_phone=recipient_phone,
                message_text=self.message_text,
                account_name=account_name,
                priority=priority,
                is_new_chat=not bool(recipient_id)  # Если нет ID, считаем новым чатом
            )
            
            return task
            
        except Exception as e:
            self.logger.error(f"Ошибка создания задачи для {recipient}: {e}")
            return None
    
    def get_next_task(self) -> Optional[MessageTask]:
        """Получить следующую задачу из очереди"""
        try:
            if not self.message_queue.empty():
                return self.message_queue.get_nowait()
            return None
        except queue.Empty:
            # Это нормально - очередь пуста
            return None
        except Exception as e:
            self.logger.error(f"Непредвиденная ошибка при получении задачи: {type(e).__name__}: {e}", 
                            exc_info=True)
            return None
    
    def requeue_failed_task(self, task: MessageTask, max_retries: int = 3):
        """Вернуть неудачную задачу в очередь с увеличением счетчика попыток"""
        try:
            task.retry_count += 1
            
            if task.retry_count <= max_retries:
                # Понижаем приоритет для повторных попыток
                task.priority = max(1, task.priority - 1)
                self.message_queue.put(task)
                self.logger.info(f"Задача возвращена в очередь (попытка {task.retry_count})")
            else:
                self.failed_messages.append(task)
                self.logger.warning(f"Задача отправлена в список неудачных после {max_retries} попыток")
        except Exception as e:
            self.logger.error(f"Ошибка при повторной постановке задачи в очередь: {type(e).__name__}: {e}")
            # В случае ошибки добавляем в список неудачных
            self.failed_messages.append(task)
    
    def mark_task_completed(self, task: MessageTask):
        """Отметить задачу как выполненную"""
        self.completed_messages.append(task)
        self.logger.debug(f"Задача выполнена для аккаунта {task.account_name}")
    
    def get_queue_stats(self) -> Dict:
        """Получить статистику очереди"""
        total_recipients = len(getattr(self, 'recipients', []))
        completed_count = len(self.completed_messages)
        
        # Безопасный расчет процента завершения
        if total_recipients > 0:
            completion_rate = (completed_count / total_recipients) * 100
        else:
            completion_rate = 0.0
            
        return {
            'pending_tasks': self.message_queue.qsize(),
            'completed_tasks': completed_count,
            'failed_tasks': len(self.failed_messages),
            'total_recipients': total_recipients,
            'completion_rate': completion_rate
        }
    
    def redistribute_tasks(self, failed_account: str, available_accounts: List[str]):
        """Перераспределить задачи с заблокированного аккаунта"""
        if not available_accounts:
            self.logger.warning("Нет доступных аккаунтов для перераспределения")
            return 0
        
        redistributed = 0
        temp_tasks = []
        
        # Извлекаем все задачи из очереди
        while not self.message_queue.empty():
            try:
                task = self.message_queue.get_nowait()
                
                if task.account_name == failed_account:
                    # Назначаем новый аккаунт
                    task.account_name = random.choice(available_accounts)
                    redistributed += 1
                    
                temp_tasks.append(task)
            except queue.Empty:
                # Очередь опустела во время обработки
                break
            except Exception as e:
                self.logger.error(f"Ошибка при перераспределении задач: {type(e).__name__}: {e}")
                break
        
        # Возвращаем задачи в очередь
        for task in temp_tasks:
            try:
                self.message_queue.put(task)
            except Exception as e:
                self.logger.error(f"Ошибка при возврате задачи в очередь: {type(e).__name__}: {e}")
                # Добавляем в список неудачных если не можем вернуть в очередь
                self.failed_messages.append(task)
        
        self.logger.info(f"Перераспределено {redistributed} задач с аккаунта {failed_account}")
        return redistributed
    
    def clear_queue(self):
        """Очистить очередь"""
        try:
            while not self.message_queue.empty():
                try:
                    self.message_queue.get_nowait()
                except queue.Empty:
                    break
        except Exception as e:
            self.logger.error(f"Ошибка при очистке очереди: {type(e).__name__}: {e}")
        
        self.completed_messages.clear()
        self.failed_messages.clear()
        self.logger.info("Очередь очищена")
    
    def save_failed_messages(self, filename: str = "data/failed_messages.json"):
        """Сохранить неудачные сообщения в файл"""
        try:
            failed_data = []
            for task in self.failed_messages:
                failed_data.append({
                    'recipient_id': task.recipient_id,
                    'recipient_username': task.recipient_username,
                    'recipient_phone': task.recipient_phone,
                    'message_text': task.message_text,
                    'account_name': task.account_name,
                    'retry_count': task.retry_count
                })
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(failed_data, f, ensure_ascii=False, indent=2)
                
            self.logger.info(f"Сохранено {len(failed_data)} неудачных сообщений в {filename}")
            
        except Exception as e:
            self.logger.error(f"Ошибка сохранения неудачных сообщений: {e}")
    
    def estimate_completion_time(self, available_accounts: int, avg_delay: float = 3.0) -> float:
        """Оценить время завершения рассылки"""
        if available_accounts == 0:
            return float('inf')
        
        pending_tasks = self.message_queue.qsize()
        estimated_seconds = (pending_tasks / available_accounts) * avg_delay
        
        return estimated_seconds