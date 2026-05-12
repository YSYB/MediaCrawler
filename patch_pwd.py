# -*- coding: utf-8 -*-
# Windows 兼容补丁：在 aiomysql 导入前设置 USERNAME 环境变量
import os
os.environ['USERNAME'] = os.environ.get('USERNAME', 'YS')
