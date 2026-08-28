import json
import os
import random
import threading
import time
from queue import Empty, Queue

from tqdm import tqdm


MAX_TASK_ATTEMPTS = 3
TRANSIENT_HTTP_ERROR_MARKERS = (
    "connection aborted",
    "connection reset",
    "connectionerror",
    "connectionreseterror",
    "connecttimeout",
    "proxyerror",
    "read timed out",
    "remotedisconnected",
    "timeout",
)


def _is_transient_http_error(error):
    error_text = f"{type(error).__name__}: {error}".lower()
    return any(marker in error_text for marker in TRANSIENT_HTTP_ERROR_MARKERS)


class MultiProcessor:
    def __init__(
        self,
        llm,
        parse_method,
        data_template,
        prompt_template,
        correction_template,
        validator,
        time_limit=300,
        back_up_llm=None,
        temperature=1,
        IsPromptList=False,
        checkpoint_dir=None,
    ):
        self.llm = llm
        self.back_up_llm = back_up_llm
        self.parse_method = parse_method
        self.data_template = data_template
        self.prompt_template = prompt_template
        self.correction_template = correction_template
        self.validator = validator
        self.time_limit = time_limit
        self.checkpoint_dir = checkpoint_dir if checkpoint_dir else "checkpoint"
        self.temperature = temperature
        self.IsPromptList = IsPromptList
        self.checkpoint_path_0 = os.path.join(self.checkpoint_dir, "checkpoint_0.json")
        self.checkpoint_path_1 = os.path.join(self.checkpoint_dir, "checkpoint_1.json")
        self.choose_checkpoint = self.initialize_checkpoint_choice()

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dict):
            return {str(k): MultiProcessor._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [MultiProcessor._json_safe(item) for item in value]
        if isinstance(value, set):
            return [MultiProcessor._json_safe(item) for item in sorted(value, key=repr)]
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            return str(value)

    def _load_checkpoint_file(self, checkpoint_path):
        if not os.path.exists(checkpoint_path):
            return None
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as exc:
            print(f"Warning: checkpoint {checkpoint_path} is not valid JSON: {exc}.")
            return None

    def initialize_checkpoint_choice(self):
        if not os.path.exists(self.checkpoint_dir):
            os.makedirs(self.checkpoint_dir)

        if not os.path.exists(self.checkpoint_path_0):
            with open(self.checkpoint_path_0, "w", encoding="utf-8") as f:
                json.dump({}, f)

        if not os.path.exists(self.checkpoint_path_1):
            with open(self.checkpoint_path_1, "w", encoding="utf-8") as f:
                json.dump({}, f)

        size_0 = os.path.getsize(self.checkpoint_path_0)
        size_1 = os.path.getsize(self.checkpoint_path_1)
        return size_1 >= size_0

    def generate_prompt(self, **kwargs):
        kwargs["data_template"] = self.data_template
        for i in range(1, 11):
            key = f"data_template{str(i).zfill(2)}"
            if key not in kwargs:
                kwargs[key] = self.data_template
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + str(key) + "}"
        if self.IsPromptList:
            selected_template = random.choice(self.prompt_template)
            prompt = selected_template.format_map(_SafeDict(kwargs))
        else:
            prompt = self.prompt_template.format_map(_SafeDict(kwargs))
        return prompt

    def generate_correction_prompt(self, answer, **kwargs):
        mapping = dict(kwargs)
        mapping["answer"] = answer
        mapping["data_template"] = self.data_template
        for i in range(1, 11):
            key = f"data_template{str(i).zfill(2)}"
            if key not in mapping:
                mapping[key] = self.data_template
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + str(key) + "}"
        return self.correction_template.format_map(_SafeDict(mapping))

    def task_perform(self, llm, **kwargs):
        try:
            prompt = self.generate_prompt(**kwargs)
            answer = llm.ask(prompt)
            structured_data = self.parse_method(answer)
            return structured_data
        except Exception as e:
            print(f"Error in task_perform: {str(e)}")
            raise e

    def correct_data(self, llm, answer, **kwargs):
        correction_prompt = self.generate_correction_prompt(answer, **kwargs)
        correction = llm.ask(correction_prompt)
        return self.parse_method(correction)

    def process_task(self, index, key_dict, Active_Transform):
        try:
            attempts = 0
            base_wait_time = 1
            use_backup = False

            while attempts < MAX_TASK_ATTEMPTS:
                try:
                    current_llm = self.back_up_llm if (use_backup and self.back_up_llm is not None) else self.llm
                    structured_data = self.task_perform(current_llm, **key_dict)
                    if self.validator(structured_data):
                        return self.map_answer_to_pos(structured_data) if Active_Transform else structured_data
                    corrected_answer = self.correct_data(current_llm, structured_data, **key_dict)
                    if corrected_answer and self.validator(corrected_answer):
                        return self.map_answer_to_pos(corrected_answer) if Active_Transform else corrected_answer
                    break
                except Exception as e:
                    err_msg = str(e)
                    attempts += 1
                    should_retry = attempts < MAX_TASK_ATTEMPTS
                    if "Throttling.RateQuota" in err_msg:
                        if should_retry:
                            wait_time = base_wait_time * (2 ** (attempts - 1)) + random.uniform(0, 1)
                            print(
                                f"Rate limit exceeded. Retrying in {wait_time:.2f} seconds. "
                                f"Attempt {attempts}/{MAX_TASK_ATTEMPTS}"
                            )
                            time.sleep(wait_time)
                    elif _is_transient_http_error(e):
                        if should_retry:
                            wait_time = base_wait_time * (2 ** (attempts - 1)) + random.uniform(0.5, 1.5)
                            print(
                                f"Transient HTTP error. Retrying in {wait_time:.2f} seconds. "
                                f"Attempt {attempts}/{MAX_TASK_ATTEMPTS}"
                            )
                            time.sleep(wait_time)
                    else:
                        print(f"An error occurred: {err_msg}. Attempt {attempts}/{MAX_TASK_ATTEMPTS}")

                    if should_retry and not use_backup and self.back_up_llm is not None:
                        use_backup = True
                        print(f"Switching to backup LLM to process task {index}")

            return None
        except Exception as final_error:
            print(f"Error occurred during process_task for index {index}: {str(final_error)}")
            return None

    def map_answer_to_pos(self, answer_dict):
        pos_dict = {}
        for i, (_, value) in enumerate(answer_dict.items(), start=1):
            pos_key = f"pos{i}"
            pos_dict[pos_key] = value
        return pos_dict

    def save_checkpoint(self, results):
        if not os.path.exists(self.checkpoint_dir):
            os.makedirs(self.checkpoint_dir)

        existing_results = self.load_checkpoint()
        for k, v in results.items():
            if v is not None:
                existing_results[str(k)] = self._json_safe(v)

        if self.choose_checkpoint:
            checkpoint_path = self.checkpoint_path_0
            self.choose_checkpoint = False
        else:
            checkpoint_path = self.checkpoint_path_1
            self.choose_checkpoint = True

        temp_path = f"{checkpoint_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self._json_safe(existing_results), f, ensure_ascii=False, indent=4)
        os.replace(temp_path, checkpoint_path)

    def load_checkpoint(self):
        checkpoint_path = self.checkpoint_path_1 if self.choose_checkpoint else self.checkpoint_path_0
        alternate_path = self.checkpoint_path_0 if checkpoint_path == self.checkpoint_path_1 else self.checkpoint_path_1

        checkpoint = self._load_checkpoint_file(checkpoint_path)
        if checkpoint is not None:
            return checkpoint

        alternate = self._load_checkpoint_file(alternate_path)
        if alternate is not None:
            print(f"Warning: Falling back to checkpoint at {alternate_path}.")
            return alternate

        print(f"Warning: No valid checkpoint found at {checkpoint_path}. Starting from scratch.")
        return {}

    def multitask_perform(
        self,
        index_dict,
        num_threads,
        checkpoint=10,
        Active_Reload=False,
        Active_Transform=False,
        checkpoint_dir=None,
    ):
        if checkpoint_dir:
            self.checkpoint_dir = checkpoint_dir
            self.checkpoint_path_0 = os.path.join(self.checkpoint_dir, "checkpoint_0.json")
            self.checkpoint_path_1 = os.path.join(self.checkpoint_dir, "checkpoint_1.json")
            self.choose_checkpoint = self.initialize_checkpoint_choice()

        if Active_Reload:
            previous_results = self.load_checkpoint()
            results = {str(k): v for k, v in previous_results.items()}
            index_dict = {k: v for k, v in index_dict.items()}
            completed_tasks = {str(k): v for k, v in results.items() if v is not None}
            remaining_tasks = {
                k: v for k, v in index_dict.items() if str(k) not in completed_tasks
            }

            print(f"Initial task count: {len(index_dict)}")
            print(f"Completed task count: {len(completed_tasks)}")
            print(f"Remaining task count: {len(remaining_tasks)}")
        else:
            results = {}
            remaining_tasks = index_dict

        queue = Queue()
        checkpoint_counter = 0
        checkpoint_interval = max(1, int(checkpoint))
        results_lock = threading.Lock()
        checkpoint_lock = threading.Lock()

        for index, key_dict in remaining_tasks.items():
            queue.put((str(index), key_dict))

        def worker(pbar):
            nonlocal checkpoint_counter
            while True:
                try:
                    index, key_dict = queue.get_nowait()
                except Empty:
                    break

                try:
                    if index in results and results[index] is not None:
                        continue

                    result = self.process_task(index, key_dict, Active_Transform)
                    if result is not None:
                        with results_lock:
                            results[index] = self._json_safe(result)

                    should_save = False
                    current_counter = 0
                    with checkpoint_lock:
                        checkpoint_counter += 1
                        current_counter = checkpoint_counter
                        should_save = checkpoint_counter % checkpoint_interval == 0

                    if should_save:
                        print(f"Saving checkpoint at counter {current_counter}")
                        with results_lock:
                            snapshot = results.copy()
                        self.save_checkpoint(snapshot)
                finally:
                    queue.task_done()
                    pbar.update(1)

        with tqdm(total=len(remaining_tasks)) as pbar:
            threads = []
            for _ in range(min(num_threads, len(remaining_tasks))):
                thread = threading.Thread(target=worker, args=(pbar,))
                threads.append(thread)
                thread.start()

            queue.join()

            for thread in threads:
                thread.join()

        with results_lock:
            final_snapshot = results.copy()
        self.save_checkpoint(final_snapshot)

        completed_tasks = sum(1 for r in final_snapshot.values() if r is not None)
        print(f"Final results - Total tasks: {len(final_snapshot)}, Completed tasks: {completed_tasks}")

        ordered_results = {
            str(k): final_snapshot[str(k)]
            for k in index_dict.keys()
            if str(k) in final_snapshot
        }
        return ordered_results

    def multitask_manage(
        self,
        index_dict,
        num_threads,
        checkpoint=10,
        Active_Reload=False,
        Active_Transform=False,
        checkpoint_dir=None,
        threshold=None,
        max_multitask_retries=None,
        max_time=None,
    ):
        start_time = time.time()
        total_tasks = len(index_dict)
        retry_count = 0
        results = {}

        while True:
            if max_time and (time.time() - start_time) >= max_time:
                print("Reached maximum time limit.")
                break

            if max_multitask_retries and retry_count >= max_multitask_retries:
                print("Reached maximum retry limit.")
                break

            if retry_count == 0:
                results = self.multitask_perform(
                    index_dict, num_threads, checkpoint, Active_Reload, Active_Transform, checkpoint_dir
                )
            else:
                results = self.multitask_perform(index_dict, num_threads, checkpoint, True, Active_Transform, checkpoint_dir)

            completed_tasks = sum(1 for r in results.values() if r is not None)
            completion_ratio = completed_tasks / total_tasks

            print(f"Completed {completed_tasks}/{total_tasks} tasks. Completion ratio: {completion_ratio:.2f}")

            if threshold is None or completion_ratio >= threshold:
                print("Reached or exceeded completion threshold.")
                break

            retry_count += 1
            print(f"Starting retry {retry_count}")

        return results
