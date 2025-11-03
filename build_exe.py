#!/usr/bin/env python3
"""
Скрипт для сборки exe файла с включенным .env файлом
"""

import os
import sys
import subprocess
import shutil

def build_exe():
    """Сборка exe файла с PyInstaller"""
    
    print("🔨 Начинаем сборку TelegramSender v2.4...")
    
    # Проверяем что PyInstaller установлен
    try:
        import PyInstaller
        print(f"✅ PyInstaller найден: {PyInstaller.__version__}")
    except ImportError:
        print("❌ PyInstaller не установлен. Устанавливаем...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
        print("✅ PyInstaller установлен")
    
    # Проверяем важные файлы
    required_files = [
        '.env',
        'main.py', 
        'api_config.py',
        'config.json',
        'src/member_collector.py',
        'src/account_manager.py',
        'src/auth_manager.py',
        'src/message_queue.py',
        'src/rate_limiter.py',
        'src/sender.py',
        'src/smart_scheduler.py'
    ]
    
    missing_files = []
    for file_path in required_files:
        if not os.path.exists(file_path):
            missing_files.append(file_path)
        else:
            print(f"✅ {file_path}")
    
    if missing_files:
        print(f"❌ Отсутствуют важные файлы: {missing_files}")
        return False
    
    print("✅ Все необходимые файлы найдены")
    
    # Создаем spec файл для PyInstaller
    spec_content = '''
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('.env', '.'),
        ('src', 'src'),
        ('data', 'data'),
        ('api_config.py', '.'),
        ('config.json', '.'),
        ('message.txt', '.'),
    ],
    hiddenimports=[
        'telethon',
        'telethon.client',
        'telethon.tl',
        'telethon.tl.types',
        'telethon.errors',
        'aioconsole', 
        'dotenv',
        'asyncio',
        'json',
        'logging',
        'time',
        'random',
        'os',
        'sys',
        'pathlib',
        'configparser',
        're',
        'typing',
        'src.account_manager',
        'src.auth_manager',
        'src.member_collector',
        'src.message_queue',
        'src.rate_limiter',
        'src.sender',
        'src.smart_scheduler',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TelegramSender_v2.4',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
'''
    
    # Записываем spec файл
    with open('TelegramSender_v2.4.spec', 'w', encoding='utf-8') as f:
        f.write(spec_content)
    
    print("✅ Spec файл создан: TelegramSender_v2.4.spec")
    
    # Запускаем PyInstaller
    try:
        print("🔄 Запускаем PyInstaller...")
        result = subprocess.run([
            sys.executable, "-m", "PyInstaller", 
            "--clean",
            "TelegramSender_v2.4.spec"
        ], check=True, capture_output=True, text=True)
        
        print("✅ Сборка завершена успешно!")
        
        # Проверяем что exe файл создан
        exe_path = os.path.join("dist", "TelegramSender_v2.4.exe")
        if os.path.exists(exe_path):
            file_size = os.path.getsize(exe_path) / (1024 * 1024)  # MB
            print(f"📦 Exe файл создан: {exe_path}")
            print(f"📏 Размер файла: {file_size:.1f} MB")
            
            # Копируем .env файл рядом с exe (на всякий случай)
            dist_env_path = os.path.join("dist", ".env")
            shutil.copy2(".env", dist_env_path)
            print(f"📄 .env файл скопирован в dist/")
            
            return True
        else:
            print("❌ Exe файл не найден после сборки")
            return False
            
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка при сборке: {e}")
        print("Вывод ошибки:")
        print(e.stderr)
        return False

def clean_build():
    """Очистка временных файлов сборки"""
    print("🧹 Очистка временных файлов...")
    
    dirs_to_remove = ['build', '__pycache__', 'src/__pycache__']
    files_to_remove = ['TelegramSender_v2.4.spec']
    
    for dir_name in dirs_to_remove:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
            print(f"🗑️ Удалена папка: {dir_name}")
    
    for file_name in files_to_remove:
        if os.path.exists(file_name):
            os.remove(file_name)
            print(f"🗑️ Удален файл: {file_name}")

if __name__ == "__main__":
    try:
        success = build_exe()
        
        if success:
            print("\n🎉 СБОРКА ЗАВЕРШЕНА УСПЕШНО!")
            print("📦 Exe файл находится в папке dist/")
            print("🔑 .env файл включен в exe и скопирован рядом")
            
            clean_choice = input("\nОчистить временные файлы сборки? (y/n): ")
            if clean_choice.lower() == 'y':
                clean_build()
        else:
            print("\n❌ СБОРКА НЕ УДАЛАСЬ")
            
    except KeyboardInterrupt:
        print("\n❌ Сборка прервана пользователем")
    except Exception as e:
        print(f"\n💥 Критическая ошибка: {e}")