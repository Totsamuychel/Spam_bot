import asyncio
import logging
from typing import Optional, Dict, Any
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, UserNotMutualContactError,
    ChatWriteForbiddenError, UserBannedInChannelError, SlowModeWaitError,
    PeerFloodError, AuthKeyUnregisteredError
)
from message_queue import MessageTask

class MessageSender:
    """Основная логика отправки сообщений через Telegram"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Настройки таймаутов
        self.RESOLVE_TIMEOUT = 15.0  # Таймаут поиска получателя
        self.SEND_TIMEOUT = 30.0     # Таймаут отправки сообщения
        self.MAX_FLOOD_WAIT = 3600   # Максимальное время ожидания FloodWait (1 час)
        
    async def send_message(self, client: TelegramClient, task: MessageTask) -> Dict[str, Any]:
        """
        Отправка сообщения через Telegram клиент с таймаутами
        Возвращает результат отправки с деталями
        """
        try:
            # Определяем получателя с таймаутом
            try:
                recipient = await asyncio.wait_for(
                    self._resolve_recipient(client, task), 
                    timeout=self.RESOLVE_TIMEOUT
                )
            except asyncio.TimeoutError:
                return {
                    'success': False,
                    'error': 'resolve_timeout',
                    'message': f'Таймаут поиска получателя ({self.RESOLVE_TIMEOUT}с)',
                    'should_retry': True
                }
            
            if not recipient:
                return {
                    'success': False,
                    'error': 'recipient_not_found',
                    'message': 'Не удалось найти получателя',
                    'should_retry': False
                }
            
            # Отправляем сообщение с таймаутом
            try:
                message = await asyncio.wait_for(
                    client.send_message(recipient, task.message_text),
                    timeout=self.SEND_TIMEOUT
                )
            except asyncio.TimeoutError:
                return {
                    'success': False,
                    'error': 'send_timeout',
                    'message': f'Таймаут отправки сообщения ({self.SEND_TIMEOUT}с)',
                    'should_retry': True
                }
            
            # Получаем информацию об отправителе с таймаутом
            try:
                me = await asyncio.wait_for(client.get_me(), timeout=5.0)
                sender_info = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            except asyncio.TimeoutError:
                sender_info = "Unknown"
            
            self.logger.info(f"✅ Сообщение отправлено от {sender_info} получателю {recipient.id} через аккаунт {task.account_name}")
            
            return {
                'success': True,
                'message_id': message.id,
                'recipient': str(recipient),
                'recipient_id': recipient.id,
                'account': task.account_name,
                'sender_info': sender_info
            }
            
        except FloodWaitError as e:
            # Превышен лимит скорости - улучшенная обработка
            wait_seconds = min(e.seconds, self.MAX_FLOOD_WAIT)  # Ограничиваем максимальное ожидание
            
            if e.seconds > self.MAX_FLOOD_WAIT:
                self.logger.error(f"Критический FloodWait для {task.account_name}: {e.seconds}с (больше {self.MAX_FLOOD_WAIT}с)")
                return {
                    'success': False,
                    'error': 'critical_flood_wait',
                    'wait_seconds': e.seconds,
                    'message': f'Критический FloodWait: {e.seconds}с',
                    'should_retry': False,
                    'should_block_account': True
                }
            else:
                self.logger.warning(f"FloodWait для {task.account_name}: {e.seconds}с")
                return {
                    'success': False,
                    'error': 'flood_wait',
                    'wait_seconds': wait_seconds,
                    'message': f'Нужно подождать {wait_seconds} секунд',
                    'should_retry': True,
                    'should_block_account': e.seconds > 300  # Блокируем если ждать больше 5 минут
                }
            
        except PeerFloodError:
            # Слишком много запросов к новым пользователям
            self.logger.error(f"PeerFlood для {task.account_name} - аккаунт временно ограничен")
            return {
                'success': False,
                'error': 'peer_flood',
                'message': 'Аккаунт ограничен из-за слишком большого количества запросов',
                'should_retry': False,
                'should_block_account': True
            }
            
        except UserPrivacyRestrictedError:
            # Пользователь запретил сообщения от незнакомцев
            self.logger.debug(f"Пользователь запретил сообщения: {task.recipient_id or task.recipient_username}")
            return {
                'success': False,
                'error': 'privacy_restricted',
                'message': 'Пользователь запретил сообщения от незнакомцев',
                'should_retry': False
            }
            
        except UserNotMutualContactError:
            # Пользователь не в контактах
            self.logger.debug(f"Пользователь не в контактах: {task.recipient_id or task.recipient_username}")
            return {
                'success': False,
                'error': 'not_mutual_contact',
                'message': 'Пользователь не в взаимных контактах',
                'should_retry': False
            }
            
        except ChatWriteForbiddenError:
            # Запрещено писать в чат
            self.logger.debug(f"Запрещено писать в чат: {task.recipient_id or task.recipient_username}")
            return {
                'success': False,
                'error': 'write_forbidden',
                'message': 'Запрещено писать в этот чат',
                'should_retry': False
            }
            
        except UserBannedInChannelError:
            # Пользователь заблокирован в канале
            self.logger.warning(f"Аккаунт {task.account_name} заблокирован")
            return {
                'success': False,
                'error': 'user_banned',
                'message': 'Аккаунт заблокирован',
                'should_retry': False,
                'should_block_account': True
            }
            
        except AuthKeyUnregisteredError:
            # Сессия недействительна
            self.logger.error(f"Недействительная сессия для {task.account_name}")
            return {
                'success': False,
                'error': 'auth_key_unregistered',
                'message': 'Сессия недействительна, требуется повторная авторизация',
                'should_retry': False,
                'should_block_account': True
            }
            
        except SlowModeWaitError as e:
            # Медленный режим в чате
            self.logger.debug(f"Медленный режим: ждем {e.seconds}с")
            return {
                'success': False,
                'error': 'slow_mode',
                'wait_seconds': e.seconds,
                'message': f'Медленный режим: ждем {e.seconds}с',
                'should_retry': True
            }
            
        except Exception as e:
            # Неожиданная ошибка
            self.logger.error(f"Неожиданная ошибка при отправке: {e}")
            return {
                'success': False,
                'error': 'unexpected_error',
                'message': str(e),
                'should_retry': True
            }
    
    async def _resolve_recipient(self, client: TelegramClient, task: MessageTask):
        """Определение получателя сообщения с таймаутами"""
        try:
            self.logger.info(f"🔍 Ищу получателя: ID={task.recipient_id}, Username={task.recipient_username}, Phone={task.recipient_phone}")
            
            # Изменяем приоритет: username > ID > phone (username более надежен для новых контактов)
            if task.recipient_username:
                username = task.recipient_username.lstrip('@')
                self.logger.info(f"📋 Поиск по Username: {username}")
                try:
                    entity = await asyncio.wait_for(client.get_entity(username), timeout=10.0)
                    self.logger.info(f"✅ Найден получатель по Username: {entity.first_name}")
                    return entity
                except asyncio.TimeoutError:
                    self.logger.warning(f"⚠️ Таймаут поиска по username: {username}")
                except Exception as e:
                    self.logger.warning(f"⚠️ Не удалось найти по username: {e}")
            
            if task.recipient_id:
                self.logger.info(f"📋 Поиск по User ID: {task.recipient_id}")
                try:
                    entity = await asyncio.wait_for(client.get_entity(task.recipient_id), timeout=10.0)
                    self.logger.info(f"✅ Найден получатель по ID: {entity.first_name}")
                    return entity
                except asyncio.TimeoutError:
                    self.logger.warning(f"⚠️ Таймаут поиска по ID: {task.recipient_id}")
                except Exception as e:
                    self.logger.warning(f"⚠️ Не удалось найти по ID: {e}")
            
            if task.recipient_phone:
                self.logger.info(f"📋 Поиск по телефону: {task.recipient_phone}")
                try:
                    entity = await asyncio.wait_for(client.get_entity(task.recipient_phone), timeout=10.0)
                    self.logger.info(f"✅ Найден получатель по телефону: {entity.first_name}")
                    return entity
                except asyncio.TimeoutError:
                    self.logger.warning(f"⚠️ Таймаут поиска по телефону: {task.recipient_phone}")
                except Exception as e:
                    self.logger.warning(f"⚠️ Не удалось найти по телефону: {e}")
            
            self.logger.error("❌ Не удалось найти получателя ни одним способом")
            return None
                
        except Exception as e:
            self.logger.error(f"❌ Критическая ошибка поиска получателя: {e}")
            return None
    
    async def test_account_connection(self, client: TelegramClient, account_name: str) -> Dict[str, Any]:
        """Тестирование подключения аккаунта"""
        try:
            # Получаем информацию о себе
            me = await client.get_me()
            
            # Проверяем возможность отправки сообщения самому себе
            test_message = await client.send_message('me', 'Тест подключения')
            await test_message.delete()
            
            return {
                'success': True,
                'account_info': {
                    'id': me.id,
                    'username': me.username,
                    'first_name': me.first_name,
                    'phone': me.phone
                }
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка тестирования аккаунта {account_name}: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_account_limits_status(self, client: TelegramClient) -> Dict[str, Any]:
        """Получение информации о лимитах аккаунта"""
        try:
            # Получаем базовую информацию об аккаунте
            me = await client.get_me()
            
            # Проверяем статус аккаунта
            full_user = await client.get_entity(me.id)
            
            return {
                'success': True,
                'is_premium': getattr(me, 'premium', False),
                'is_verified': getattr(me, 'verified', False),
                'is_restricted': getattr(full_user, 'restricted', False),
                'restriction_reason': getattr(full_user, 'restriction_reason', None)
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка получения статуса лимитов: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_send_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Анализ результата отправки для принятия решений"""
        analysis = {
            'should_continue': True,
            'should_switch_account': False,
            'should_wait': False,
            'wait_time': 0,
            'error_severity': 'low'
        }
        
        if result['success']:
            return analysis
        
        error_type = result.get('error', 'unknown')
        
        # Критические ошибки - нужно заблокировать аккаунт
        if error_type in ['peer_flood', 'user_banned', 'auth_key_unregistered']:
            analysis.update({
                'should_continue': False,
                'should_switch_account': True,
                'error_severity': 'critical'
            })
        
        # Ошибки с ожиданием
        elif error_type in ['flood_wait', 'slow_mode']:
            wait_time = result.get('wait_seconds', 60)
            analysis.update({
                'should_wait': True,
                'wait_time': wait_time,
                'should_switch_account': wait_time > 60,  # Переключаем если ждать больше минуты
                'error_severity': 'medium' if wait_time < 300 else 'high'
            })
        
        # Ошибки получателя - продолжаем с другими
        elif error_type in ['privacy_restricted', 'not_mutual_contact', 'write_forbidden', 'recipient_not_found']:
            analysis.update({
                'should_continue': True,
                'error_severity': 'low'
            })
        
        return analysis