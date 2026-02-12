@echo off
chcp 65001 > nul
title 学会タイムキーパー v0.2

echo ==========================================
echo   学会タイムキーパー v0.2 起動中...
echo ==========================================

:: 仮想環境がある場合は有効化（なければスキップ）
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

:: 必要パッケージの確認・インストール
pip show fastapi >nul 2>&1 || pip install fastapi uvicorn python-socketio aiofiles qrcode pillow

echo.
echo  ブラウザでアクセス:
echo  http://localhost:8000/
echo.
echo  終了: Ctrl+C を押してください
echo ==========================================
echo.

python timekeeper_server.py

pause
