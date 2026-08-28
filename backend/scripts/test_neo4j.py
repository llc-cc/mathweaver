# test_neo4j.py
import os
import sys

# 加载 .env
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.config import load_env_file
load_env_file()

from neo4j import GraphDatabase, exceptions

URI = os.environ.get("NEO4J_URI", "")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

try:
    # 建立连接并测试
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    with driver.session() as session:
        result = session.run("RETURN 1")
        print("✅ Neo4j 连接成功！")
        print("测试结果:", result.single()[0])
    driver.close()
except exceptions.AuthError as e:
    print(f"❌ 身份验证失败：{e}")
    print("排查方向：密码错误/用户名错误/URI 对应实例不匹配")
except exceptions.ServiceUnavailable as e:
    print(f"❌ 服务不可达：{e}")
    print("排查方向：URI 错误/实例未启动/网络不通")
except Exception as e:
    print(f"❌ 其他错误：{e}")