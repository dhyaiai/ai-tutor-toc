"""生成测试用 access/refresh token(version=0),供浏览器注入复现 bug。"""
import sys, uuid
sys.path.insert(0, r"d:\AICoding\AI_zhujiao\backend")
from app.core.security import create_access_token, create_refresh_token
from app.core.config import get_settings

s = get_settings()
print(create_access_token(1, s, token_version=0))
print(create_refresh_token(1, str(uuid.uuid4()), s, token_version=0))
