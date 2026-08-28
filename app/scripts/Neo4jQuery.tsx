import neo4j from 'neo4j-driver';
import { Record as Neo4jRecord } from 'neo4j-driver';


interface Neo4j_Info {
    uri: string;
    user: string;
    password: string;
}

/**
 * Neo4j 数据库查询类
 * @member driver - Neo4j 驱动实例
 */
class Neo4jQuery {
    driver : any;
    
    /**
     * 构造函数，创建到 Neo4j 数据库的连接并通过 info 登录
     * @param info - 数据库连接信息
     */
    constructor(info : Neo4j_Info) {
        this.driver = neo4j.driver(
            info.uri, 
            neo4j.auth.basic(info.user, info.password)
        );
    }

    /**
     * 更新数据库连接信息
     * @param info - 数据库连接信息
     */
    updateInfo(info: Neo4j_Info) {
        this.driver = neo4j.driver(
            info.uri,
            neo4j.auth.basic(info.user, info.password)
        );
    }

    /**
     * 关闭 Neo4j 数据库连接
     */
    async close() {
    // 关闭 Neo4j 数据库连接
        await this.driver.close();
    }

    /**
     * 工具函数，将字典转化为 Cypher 代码格式
     * @param d - 字典对象
     * @returns Cypher 格式的字符串
     */
    static d2s(d: Record<string, any>) : string {
        let result = "";
        for (const [key, value] of Object.entries(d)) {
            if (typeof value === "string") {
                const escapedValue = value.replace(/"/g, '\\"');
                result += `${key}: "${escapedValue}", `;
            }
        }
        
        // Remove the trailing comma and space
        if (result) {
            result = result.slice(0, -2); // Remove the last ", "
        }
        
        return "{" + result + "}";
    }

    /**
     * 将字典扁平化，避免嵌套对象在 Neo4j 中存储出错
     * @param dict - 待扁平化的字典
     */
    static flatten (dict: Record<string, any>) : Record<string, any> {
        let result: Record<string, any> = {};

        for (const [key, value] of Object.entries(dict)) {
            if (typeof value === "object" && value !== null) {
                result[key] = "@stringified_from_JSON@" + JSON.stringify(value);
            } else {
                result[key] = value;
            }
        }
        return result;
    }

    /**
     * 将扁平化的字典复原为嵌套字典
     * @param dict - 待复原的字典
     */
    static unflatten (dict: Record<string, any>) : Record<string, any> {
        let result: Record<string, any> = {};

        for (const [key, value] of Object.entries(dict)) {
            if (value.includes("@stringified_from_JSON@")) {
                result[key] = JSON.parse(value.replace("@stringified_from_JSON@", ""));
            } else {
                result[key] = value;
            }
        }
        return result;
    }
    
    /**
     * 执行 Cypher 查询
     * @param query - Cypher 代码
     * @returns - 查询结果记录列表
     */
    async makeQuery(query: string) : Promise<Neo4jRecord[]> {
        let session;
        try {
            session = this.driver.session()
            console.log("Executed query:", query);
            const result = await session.run(query);
            return result.records;
        } catch (error) {
            console.error("Error executing query:", error);
            return [];
        }
        finally {
            await session.close();
        }
    }
    
    /////////////////////////////////////////////////////////////////////////////////////////////
    // 增

    /**
     * 通过性质创建新节点
     * @param {string} label - 节点标签
     * @param {dict} properties - 节点属性
     */
    async createNode(label: string, properties: Record<string, any>) {
        const query = `MERGE (n:${label}${Neo4jQuery.d2s(Neo4jQuery.flatten(properties))})`;
        await this.makeQuery(query);
    }

    /**
     * 通过 UUID 创建新关系
     * @param {string} uuid1 - 始节点 UUID
     * @param {string} uuid2 - 终节点 UUID
     * @param {string} labelr - 关系标签
     */
    async createRelation(uuid1: string, uuid2: string, labelr: string) {
        const query = `
        MATCH (a),(b)
        WHERE a.uuid = "${uuid1}" AND b.uuid = "${uuid2}"
        MERGE (a)-[r:${labelr}]->(b)`;
        await this.makeQuery(query);
    }

    /**
     * 整图创建
     * @param node_list - 节点列表
     * @param relations_list - 关系列表
     */
    async createGraph(node_list: {label: string, [key: string]: any}[], relations_list: {from: string, to: string, label: string}[]) {
        for (const node of node_list) {
            await this.createNode(node.label, Neo4jQuery.flatten(node));
        }

        for (const relation of relations_list) {
            await this.createRelation(relation.from, relation.to, relation.label);
        }
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////
    // 删

    /**
     * 删除全部节点和关系，危险！会直接清空数据库。
     * @param {string} password - 确认密码，必须是 "我已确认该方法会直接删除所有节点和关系"
     */
    async deleteAll(password: string) {
        if (password === "我已确认该方法会直接删除所有节点和关系") {
            const query = "MATCH (n) DETACH DELETE n";
            await this.makeQuery(query);
        }
    }

    /**
     * 删除指定标签和 ID 的节点及与之相连的所有关系
     * @param {string} label 
     * @param {string} uuid 
     */
    async deleteNode(label: string, uuid: string) {
    // 以后要确保 next 链表重排
        const query = `
        MATCH (n:${label})
        WHERE n.uuid = "${uuid}"
        DETACH DELETE n`;
        await this.makeQuery(query);
    }

    /**
     * 删除指定关系
     * @param {string} uuid1 - 起始节点 UUID
     * @param {string} uuid2 - 终节点 UUID
     * @param {string} labelr - 关系标签
     */
    async deleteRelation(uuid1: string, uuid2: string, labelr: string) {
        const query = `
        MATCH (a)-[r:${labelr}]->(b)
        WHERE a.uuid = "${uuid1}" AND b.uuid = "${uuid2}"
        DELETE r`;
        await this.makeQuery(query);
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////
    // 改

    async updateNode(label: string, uuid: string, properties: Record<string, any>) {
        /**
         * Update properties of specified node
         */
        const query = `
        MATCH (n:${label}) WHERE n.uuid = "${uuid}"
        SET n += ${Neo4jQuery.d2s(Neo4jQuery.flatten(properties))}`;
        await this.makeQuery(query);
    }

    async updateRelation(uuid1: string, uuid2: string, labelr: string, properties: Record<string, any>) {
        /**
         * Update properties of specified relationship
         */
        const query = `
        MATCH (a)-[r:${labelr}]->(b)
        WHERE a.uuid = "${uuid1}" AND b.uuid = "${uuid2}"
        SET r += ${Neo4jQuery.d2s(Neo4jQuery.flatten(properties))}`;
        await this.makeQuery(query);
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////
    // 查

    async getAllTerritory() {
        const query = `
        MATCH (n)
        WHERE n.territory IS NOT NULL
        RETURN DISTINCT n.territory AS value
        ORDER BY value;
        `
        const response : Neo4jRecord[] = await this.makeQuery(query);
        return response.map(record => record.get("n").properties.territory);
    }

    /**
     * 查询所有节点
     * @param label - 节点标签，默认为空字符串表示查询所有标签节点
     * @return - 所有节点列表
     */
    async getAllNodes(label: string = "") {
        const query = `
            MATCH (n${label? `:${label}` : ""})
            RETURN n
        `;

        const response : Neo4jRecord[] = await this.makeQuery(query);

        return response.map(record => Object.assign(Neo4jQuery.unflatten(record.get("n").properties), {id: record.get("n").identity.toString()}));
    }

    /**
     * 查询所有关系
     * @param label - 关系标签，默认为空字符串表示查询所有标签关系
     * @return - 所有关系列表
     */
    async getAllRelations(label: string = "") {

        const query = `
            MATCH (a)-[r${label? `:${label}` : ""}]->(b)
            RETURN a, b, r
        `;
        
        const response : Neo4jRecord[] = await this.makeQuery(query);
        
        return response.map(record => ({
            from: record.get("a").properties.uuid, 
            to: record.get("b").properties.uuid, 
            label: record.get("r").type
        }));
    }

    /**
     * 查询节点
     */
    async getNode(uuid: string, label: string = "") {
        const query = `
        MATCH (n${label? `:${label}` : ""})
        WHERE n.uuid = "${uuid}"
        RETURN n
        `;
        const response : Neo4jRecord[] = await this.makeQuery(query);

        if (response.length > 0) {
            console.log(`Node with uuid ${uuid} found: `, response.map(record => Neo4jQuery.unflatten(record.get("n").properties)));
            return Neo4jQuery.unflatten(response[0].get("n").properties);
        } else {
            console.warn(`Node with uuid ${uuid} not found.`);
            return null;
        }
    }

    /**
     * 查询节点，按性质谓词筛选
     * @param key - 键名
     * @param value - 值
     * @param label - 标签
     * @returns 查询结果
     */
    async getNodesPredicate(key: string, value: string, label: string = "") {
        const query = `
        MATCH (n${label? `:${label}` : ""})
        WHERE n.${key} = "${value}"
        RETURN n
        `;
        const response : Neo4jRecord[] = await this.makeQuery(query);
        return response.map(record => Neo4jQuery.unflatten(record.get("n").properties));
    }

    async getAllValues(label: string, key: string) {
        const query = `
        MATCH (n:${label})
        RETURN DISTINCT n.${key} AS value
        ORDER BY value;
        `
        const response : Neo4jRecord[] = await this.makeQuery(query);
        const result = response.map(record => record.get("value"));
        console.log("getAllValues result:", result);
        return result;
    }

}

const neo4j_info : Neo4j_Info = {
    uri: "neo4j://dev1.iezpark.com:8111",
    user: "neo4j",
    password: "ai4math!"
}

let querier = null;

try { querier= new Neo4jQuery(neo4j_info);} catch (error) {
    console.error("Failed to create Neo4jQuery instance:", error);
}
export const Querier = querier                          // 创建 Neo4jQuery 实例