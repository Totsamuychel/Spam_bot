"""
Telegram Multi-Account Message Sender
Модули для безопасной рассылки сообщений с мультиаккаунтной поддержкой
"""

__version__ = "1.0.0"
__author__ = "Telegram Bot Developer"

# Экспортируем основные классы для удобного импорта
from .account_manager import AccountManager
from .rate_limiter import RateLimiter
from .message_queue import MessageQueue, MessageTask
from .sender import MessageSender
from .auth_manager import AuthManager
from .member_collector import MemberCollector

__all__ = [
    'AccountManager',
    'RateLimiter', 
    'MessageQueue',
    'MessageTask',
    'MessageSender',
    'AuthManager',
    'MemberCollector'
]