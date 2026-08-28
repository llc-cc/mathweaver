
# 1.如何设计并启动一个多线程操作

## 1. 设计提示词和数据模板

首先，设计您的数据模板和提示词模板：

## 提示词模板示例

```python
prompt_template = '''
给定输入：
{pos1}

请执行以下操作并输出三个结果：
1. [操作说明1]
2. [操作说明2]
3. [操作说明3]

同时简要描述您的操作行为。

请按照以下格式输出：
{data_template}
'''
```

## 输入数据示例

```python
index_dict = {
    "1": {"pos1": "输入数据1"},
    "2": {"pos1": "输入数据2"},
    "3": {"pos1": "输入数据3"},
    # ... 更多数据
}
```

## 关系解释

1. **变量匹配**：
   - 提示词模板中的 `{pos1}` 对应输入数据字典中的 `"pos1"` 键。
   - 当处理 "1" 号任务时，`{pos1}` 将被替换为 "输入数据1"。

2. **动态替换**：
   - 对于每个任务，系统会用相应的输入数据替换模板中的变量。
   - 例如，处理 "2" 号任务时，生成的实际提示词会包含 "输入数据2"。

3. **多个变量**：
   - 如果您的任务需要多个输入，可以在模板中使用多个变量，如 `{pos1}`, `{pos2}` 等。
   - 相应地，输入数据字典中也应该有匹配的键：`{"pos1": "...", "pos2": "..."}`。

4. **数据模板**：
   - `{data_template}` 是一个特殊变量，它会被替换为您定义的 `data_template` 字符串。
   - 这告诉模型如何构造其输出。

```python

correction_template = '''
您是一位细心的校对者。我将给您一个由大型语言模型生成的数据结构。请根据指定的格式和内容对其进行校对和纠正。

校对的格式为：
{data_template}

这是需要验证的文本：{answer}。请帮我校对并纠正这个列表。
'''

def validator(data):
    # 实现您的验证逻辑
    return True
```

## 2. 准备输入数据

创建一个包含所有任务的字典：

```python
index_dict = {
    "1": {"pos1": "输入数据1"},
    "2": {"pos1": "输入数据2"},
    "3": {"pos1": "输入数据3"},
    # ... 更多数据
}
```

## 3. 初始化 MultiProcessor

```python
from your_llm_module import YourLLM  # 导入您的LLM类

llm = YourLLM(api_key="your_api_key")
parse_method = parse_dict  # 或其他解析方法：parse_list, parse_code, parse_pads

processor = MultiProcessor(
    llm=llm,
    parse_method=parse_method,
    data_template=data_template,
    prompt_template=prompt_template,
    correction_template=correction_template,
    validator=validator,
    time_limit=120,  # 单个任务的时间限制（秒）
    temperature=0.7
)
```

## 4. 启动多线程操作

使用 `multitask_perform` 方法启动多线程操作：

```python
results = processor.multitask_perform(
    index_dict=index_dict,
    num_threads=10,  # 设置线程数
    checkpoint=20,   # 每处理20个任务保存一次检查点
    Active_Reload=False,  # 是否从检查点重新加载
    Active_Transform=False,  # 是否转换结果格式
    checkpoint_dir="my_checkpoints"  # 检查点保存目录
)
```

## 5. 处理结果

处理返回的结果：

```python
for index, result in results.items():
    if result:
        print(f"Task {index} completed:")
        print(result)
    else:
        print(f"Task {index} failed.")
```

# 2. 如何管理任务完成率、重试次数和最大处理时间

要管理任务完成率、重试次数和最大处理时间，您可以使用 `multitask_manage` 方法：

```python
results = processor.multitask_manage(
    index_dict,
    num_threads=10,
    checkpoint=20,
    Active_Reload=False,
    Active_Transform=False,
    checkpoint_dir="my_checkpoints",
    threshold=0.95,  # 设置任务完成率阈值为95%
    max_multitask_retries=3,  # 最大重试次数为3次
    max_time=3600  # 最大处理时间为1小时（3600秒）
)
```

这个方法会：

1. 重复执行 `multitask_perform` 直到满足以下条件之一：
   - 达到或超过设定的完成率阈值（threshold）
   - 达到最大重试次数（max_multitask_retries）
   - 达到最大处理时间（max_time）

2. 在每次重试时，它会：
   - 从检查点重新加载已完成的任务（Active_Reload=True）
   - 只处理未完成的任务
   - 更新完成率并检查是否达到阈值

3. 输出处理进度和完成率信息

通过这种方式，您可以有效地管理大规模任务处理，确保高完成率，同时避免无限循环或过长的处理时间。