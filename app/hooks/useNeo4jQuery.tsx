// app/hooks/useNeo4jQuery.ts

import { useState, useEffect } from 'react';
import neo4j from 'neo4j-driver';

interface Neo4jConfig {
  uri: string;
  user: string;
  password: string;
}

interface GraphData {
  nodes: any[];
  edges: any[];
}

export const useNeo4jQuery = (config: Neo4jConfig) => {
  const [driver, setDriver] = useState<any>(null);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    try {
      const neo4jDriver = neo4j.driver(
        config.uri,
        neo4j.auth.basic(config.user, config.password)
      );
      setDriver(neo4jDriver);
      setIsConnected(true);
      console.log('✅ Neo4j 连接成功');
    } catch (error) {
      console.error('❌ Neo4j 连接失败:', error);
      setIsConnected(false);
    }

    return () => {
      if (driver) {
        driver.close();
      }
    };
  }, [config.uri, config.user, config.password]);

  const getAllGraph = async (): Promise<GraphData | null> => {
    if (!driver) return null;

    const session = driver.session();
    try {
      // 查询所有节点
      const nodesResult = await session.run(`
        MATCH (n:KnowledgeNode)
        RETURN n.uuid as uuid, n.env as env, n.title as title, 
               n.title_en as title_en, n.content as content
      `);

      const nodes = nodesResult.records.map((record: any) => ({
        uuid: record.get('uuid'),
        env: record.get('env'),
        title: record.get('title'),
        title_en: record.get('title_en'),
        content: record.get('content'),
      }));

      // 查询所有关系
      const edgesResult = await session.run(`
        MATCH (a:KnowledgeNode)-[r:RELATES_TO]->(b:KnowledgeNode)
        RETURN a.title as from_title, b.title as to_title,
               r.name as name, r.explanation as explanation, 
               r.strength as strength
      `);

      const edges = edgesResult.records.map((record: any) => ({
        出发节点: record.get('from_title'),
        到达节点: record.get('to_title'),
        关系名称: record.get('name'),
        关系解释: record.get('explanation'),
        关系强度: record.get('strength')?.toNumber() || 0,
      }));

      return { nodes, edges };
    } catch (error) {
      console.error('查询失败:', error);
      return null;
    } finally {
      await session.close();
    }
  };

  return { isConnected, getAllGraph };
};