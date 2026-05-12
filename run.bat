@echo off
chcp 65001 >nul
set USERNAME=%USERNAME%
set PATH=C:\nvm4w\nodejs;%PATH%
set EXECJS_RUNTIME=Node
cd /d D:\MediaCrawler
python main.py %*
