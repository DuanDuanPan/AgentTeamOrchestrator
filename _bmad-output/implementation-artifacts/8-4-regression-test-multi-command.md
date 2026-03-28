# Story 8.4: 回归测试支持多命令（功能/集成/E2E）

Status: ready-for-dev

## Story

As a 操作者,
I want 回归测试支持配置多条命令，分别执行功能测试、集成测试和 E2E 测试,
so that merge 后的回归验证覆盖更全面。

## Acceptance Criteria

1. **AC1: 多命令配置**
   - Given ato.yaml 中配置了 `regression_test_commands` 列表
   - When 调用 `load_config()` 加载配置
   - Then 配置加载成功，commands 列表按序保存

2. **AC2: 顺序执行**
   - Given regression_test_commands 包含 3 条命令（功能/集成/E2E）
   - When 触发回归测试
   - Then 按列表顺序依次执行每条命令

3. **AC3: 失败即中止**
   - Given 第 2 条命令执行失败（exit code != 0）
   - When 回归测试进行中
   - Then 立即中止后续命令，将已失败命令的输出汇总到 error_message

4. **AC4: 单命令向后兼容**
   - Given ato.yaml 中只配置了 `regression_test_command`（单数形式）且未配置 `regression_test_commands`
   - When 触发回归测试
   - Then 仍使用单命令执行，行为不变

5. **AC5: 优先级**
   - Given ato.yaml 同时配置了 `regression_test_command` 和 `regression_test_commands`
   - When 加载配置
   - Then `regression_test_commands` 优先使用，忽略单数形式

6. **AC6: 结果汇总**
   - Given 所有命令执行成功
   - When 回归测试完成
   - Then task 标记为 completed，exit_code=0

## Tasks / Subtasks

- [ ] Task 1: 配置模型扩展 (AC: #1, #4, #5)
  - [ ] 1.1 `src/ato/config.py` ATOSettings 新增 `regression_test_commands: list[str] = []`
  - [ ] 1.2 保留 `regression_test_command: str = "uv run pytest"` 做单命令兼容
  - [ ] 1.3 新增辅助方法 `get_regression_commands() -> list[str]`：commands 非空时返回 commands，否则返回 [command]

- [ ] Task 2: 回归测试执行改造 (AC: #2, #3, #6)
  - [ ] 2.1 `src/ato/merge_queue.py` `_run_regression_test()` 改为循环执行 `get_regression_commands()` 返回的命令列表
  - [ ] 2.2 每条命令独立执行，记录命令名和 exit_code
  - [ ] 2.3 任一命令失败立即中止，error_message 包含失败命令索引、命令文本和输出摘要
  - [ ] 2.4 所有命令成功时 exit_code=0

- [ ] Task 3: 更新 ato.yaml.example (AC: #1)
  - [ ] 3.1 替换单命令示例为多命令格式：
    ```yaml
    regression_test_commands:
      - "uv run pytest tests/unit/"           # 功能测试
      - "uv run pytest tests/integration/"    # 集成测试
      - "uv run pytest tests/e2e/"            # E2E 测试
    ```
  - [ ] 3.2 保留 regression_test_command 注释说明向后兼容

- [ ] Task 4: 更新测试 (AC: #1-#6)
  - [ ] 4.1 新增测试：多命令顺序执行，全部成功
  - [ ] 4.2 新增测试：中间命令失败时中止并正确汇总错误
  - [ ] 4.3 新增测试：仅配置单命令时行为不变
  - [ ] 4.4 新增测试：同时配置时 commands 优先

## Dev Notes

- `merge_queue.py:513-590` 的 `_run_regression_test()` 是唯一需要重构的执行逻辑
- 当前使用 `shlex.split(cmd)` 解析命令字符串，多命令场景下逐条解析即可
- 超时控制：每条命令共享 `timeout.structured_job` 总预算，或各自独立超时——建议各自独立使用相同超时值（简单且安全）
- error_message 截断到 1000 字符的逻辑需适配多命令汇总

### Project Structure Notes

- 改动集中在 config.py（新增字段+辅助方法）和 merge_queue.py（执行逻辑）

### References

- [Source: src/ato/config.py:116] regression_test_command 定义
- [Source: src/ato/merge_queue.py:513-590] _run_regression_test 实现
