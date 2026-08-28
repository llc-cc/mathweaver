# backend/neo4j_handler.py
from neo4j import GraphDatabase
import uuid
import json

class Neo4jHandler:
    def __init__(self, uri, user, password):
        """初始化 Neo4j 连接"""
        try:
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            # 测试连接
            with self.driver.session() as session:
                result = session.run("RETURN 1")
                result.single()
            print(f"Neo4j 连接成功: {uri}")
        except Exception as e:
            print(f"Neo4j 连接失败: {e}")
            raise
    
    def close(self):
        """关闭连接"""
        if self.driver:
            self.driver.close()
    
    def create_graph(self, nodes, edges, pdf_name=""):
        """
        将节点和边存入 Neo4j
        
        Args:
            nodes: 节点列表
            edges: 边列表
            pdf_name: PDF 文件名（用于标记来源）
        
        Returns:
            dict: 创建统计信息
        """
        with self.driver.session() as session:
            nodes_created = 0
            edges_created = 0
            node_uuid_map = {}  # title -> uuid
            
            # 1. 创建节点
            for node in nodes:
                node_uuid = str(uuid.uuid4())
                title = node.get('title', '')
                node_uuid_map[title] = node_uuid
                
                query = """
                MERGE (n:KnowledgeNode {
                    uuid: $uuid,
                    env: $env,
                    title: $title,
                    title_en: $title_en,
                    content: $content,
                    source: $source
                })
                """
                
                session.run(query,
                    uuid=node_uuid,
                    env=node.get('env', ''),
                    title=title,
                    title_en=node.get('title_en', ''),
                    content=node.get('content', ''),
                    source=pdf_name
                )
                nodes_created += 1
            
            # 2. 创建关系
            for edge in edges:
                from_title = edge.get('出发节点', '')
                to_title = edge.get('到达节点', '')
                
                from_uuid = node_uuid_map.get(from_title)
                to_uuid = node_uuid_map.get(to_title)
                
                if not from_uuid or not to_uuid:
                    continue
                
                query = """
                MATCH (a:KnowledgeNode {uuid: $from_uuid})
                MATCH (b:KnowledgeNode {uuid: $to_uuid})
                MERGE (a)-[r:RELATES_TO {
                    name: $name,
                    explanation: $explanation,
                    strength: $strength
                }]->(b)
                """
                
                session.run(query,
                    from_uuid=from_uuid,
                    to_uuid=to_uuid,
                    name=edge.get('关系名称', ''),
                    explanation=edge.get('关系解释', ''),
                    strength=int(edge.get('关系强度', 0))
                )
                edges_created += 1
            
            return {
                'nodes_created': nodes_created,
                'edges_created': edges_created
            }
    
    def get_all_graph(self):
        """获取所有节点和关系"""
        with self.driver.session() as session:
            # 查询节点
            nodes_result = session.run("""
                MATCH (n:KnowledgeNode)
                RETURN n.uuid as uuid, n.env as env, n.title as title, 
                       n.title_en as title_en, n.content as content
            """)
            
            nodes = []
            uuid_to_index = {}  # uuid -> index 映射
            
            for idx, record in enumerate(nodes_result):
                uuid_to_index[record['uuid']] = idx
                nodes.append({
                    'env': record['env'],
                    'title': record['title'],
                    'title_en': record['title_en'],
                    'content': record['content']
                })
            
            # 查询关系
            edges_result = session.run("""
                MATCH (a:KnowledgeNode)-[r:RELATES_TO]->(b:KnowledgeNode)
                RETURN a.uuid as from_uuid, a.title as from_title,
                       b.uuid as to_uuid, b.title as to_title,
                       r.name as name, r.explanation as explanation, 
                       r.strength as strength
            """)
            
            edges = []
            for record in edges_result:
                edges.append({
                    '出发节点': record['from_title'],
                    '到达节点': record['to_title'],
                    '关系名称': record['name'],
                    '关系解释': record['explanation'],
                    '关系强度': record['strength']
                })
            
            return {'nodes': nodes, 'edges': edges}
    
    def clear_database(self):
        """清空数据库"""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            print("✅ 数据库已清空")