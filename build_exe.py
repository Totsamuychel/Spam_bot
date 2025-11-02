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
    
    print("🔨 Начинаем сборку exe файла...")
    
    # Проверяем что PyInstaller установлен
    try:
        import PyInstaller
        print(f"✅ PyInstaller найден: {PyInstaller.__version__}")
    except ImportError:
        print("❌ PyInstaller не установлен. Устанавливаем...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
        print("✅ PyInstaller установлен")
    
    # Проверяем что .env файл существует
    if not os.path.exists('.env'):
        print("❌ .env файл не найден!")
        return False
    
    print("✅ .env файл найден")
    
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
    ],
    hiddenimports=[
        'telethon',
        'aioconsole', 
        'dotenv',
        'asyncio',
        'json',
        'logging',
        'time',
        'random',
        'os',
        'configparser'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='telegram_sender',
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
    with open('telegram_sender.spec', 'w', encoding='utf-8') as f:
        f.write(spec_content)
    
    print("✅ Spec файл создан")
    
    # Запускаем PyInstaller
    try:
        print("🔄 Запускаем PyInstaller...")
        result = subprocess.run([
            sys.executable, "-m", "PyInstaller", 
            "--clean",
            "telegram_sender.spec"
        ], check=True, capture_output=True, text=True)
        
        print("✅ Сборка завершена успешно!")
        
        # Проверяем что exe файл создан
        exe_path = os.path.join("dist", "telegram_sender.exe")
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
    
    dirs_to_remove = ['build', '__pycache__']
    files_to_remove = ['telegram_sender.spec']
    
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