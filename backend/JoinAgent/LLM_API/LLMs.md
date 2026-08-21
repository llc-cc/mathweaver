
# 模型池使用手册

## 1. 如何调用DeepseekLLM类或MultiLLM类进行对话(非流式)

这部分没有变化，仍然可以直接使用各自的 `ask` 方法：

```python
# DeepseekLLM
deepseek = DeepseekLLM(version='coder', api_key='your_api_key')
response = deepseek.ask("你的问题")

# MultiLLM
multi_llm = MultiLLM(model='deepseek-coder', api_key='your_api_key')
response = multi_llm.ask("你的问题")
```

## 2. 如何调用MultiLLM类进行视觉识别、嵌入

这部分也没有变化：

```python
# 视觉识别
multi_llm = MultiLLM(vision_model='gpt-4o-mini', api_key='your_api_key')
response = multi_llm.look("image_path.jpg", "描述这张图片")

# 嵌入
multi_llm = MultiLLM(embed_model='text-embedding-3-large', api_key='your_api_key')
embedding = multi_llm.embed_text("要嵌入的文本")
```

## 3. 如何初始化一个模型池，并用模型池进行对话、视觉识别、嵌入

```python
deepseek = DeepseekLLM(api_key='deepseek_api_key')
multi_llm = MultiLLM(api_key='multi_llm_api_key')

model_pool = ModelPool([deepseek, multi_llm])

# 对话
response = model_pool.ask("你的问题")

# 视觉识别
response = model_pool.look("image_path.jpg", "描述这张图片")

# 嵌入
embedding = model_pool.embed_text("要嵌入的文本")
```

## 4. 如何向模型池中增加或者删除模型

```python
# 增加模型
new_model = MultiLLM(api_key='new_api_key')
model_pool.add_model(new_model)

# 删除模型
model_pool.remove_model(existing_model)
```

## 5. 如何提高某个池中模型的访问量权重

修改后的 `set_weight` 方法现在使用权重调整因子（倍率）：

```python
model_pool.set_weight(existing_model, weight_factor)
```

例如，要将某个模型的权重提高为原来的两倍：

```python
model_pool.set_weight(existing_model, 2.0)
```

注意：调整后，所有模型的权重会自动重新归一化。

## 6. 如何同时修改所有模型的权重

使用 `update_weights` 方法可以一次性更新所有模型的权重：

```python
new_weights = [1.0, 2.0, 0.5]  # 假设有3个模型
model_pool.update_weights(new_weights)
```

如果提供的权重数量少于模型数量，会自动使用平均值补齐；如果多于模型数量，会截断多余的权重。更新后，权重会自动归一化。

## 7. 如何查看当前池中模型

使用 `get_models` 方法可以获取当前池中的所有模型及其权重：

```python
models_and_weights = model_pool.get_models()
for model, weight in models_and_weights:
    print(f"Model: {type(model).__name__}, Weight: {weight}")
```

这将返回一个包含模型实例和对应权重的列表。

另外，你还可以使用以下方法获取更多信息：

- `get_pool_size()`: 获取模型池的大小
- `get_available_operations()`: 获取池中所有可用的操作
