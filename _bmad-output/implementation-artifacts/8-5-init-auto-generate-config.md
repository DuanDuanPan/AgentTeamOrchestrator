# Story 8.5: ato init 自动生成配置文件并支持项目路径选择

Status: ready-for-dev

## Story

As a 操作者,
I want `ato init` 在目标项目路径下自动生成 ato.yaml 配置文件（如不存在），且支持指定项目路径,
so that 初始化流程更顺畅，无需手动复制配置模板。

## Acceptance Criteria

1. **AC1: 自动生成 ato.yaml**
   - Given 目标项目路径下不存在 ato.yaml
   - When 执行 `ato init`
   - Then 自动从 ato.yaml.example 复制生成 ato.yaml 到项目路径
   - And 输出提示"已生成 ato.yaml，请根据需要调整配置后运行 `ato start`"

2. **AC2: 已有配置不覆盖**
   - Given 目标项目路径下已存在 ato.yaml
   - When 执行 `ato init`
   - Then 不覆盖现有 ato.yaml，继续后续 preflight 检查

3. **AC3: example 缺失时提示**
   - Given 项目中不存在 ato.yaml.example
   - When 执行 `ato init` 且目标路径下无 ato.yaml
   - Then 输出明确错误提示，引导用户手动创建配置文件

4. **AC4: 项目路径参数**
   - Given 用户执行 `ato init /path/to/project`
   - When 指定了非当前目录的项目路径
   - Then 在指定路径下执行 preflight 检查和配置生成
   - And 数据库创建在 `<project_path>/.ato/state.db`

5. **AC5: 默认当前路径**
   - Given 用户执行 `ato init`（不带参数）
   - When 未指定项目路径
   - Then 默认使用当前工作目录作为项目路径

6. **AC6: Preflight 适配**
   - Given ato.yaml 检查项
   - When preflight 检测到无 ato.yaml
   - Then 不再报 HALT，改为 INFO 级别提示"将自动生成"

## Tasks / Subtasks

- [ ] Task 1: 自动生成配置文件 (AC: #1, #2, #3)
  - [ ] 1.1 `src/ato/cli.py` `init_command()` 中 preflight 通过后、初始化 DB 前，检测 `<project_path>/ato.yaml` 是否存在
  - [ ] 1.2 不存在时：查找 `<project_path>/ato.yaml.example`，找到则复制为 `ato.yaml`，输出成功提示
  - [ ] 1.3 ato.yaml.example 也不存在时：输出错误提示并 raise typer.Exit
  - [ ] 1.4 已存在时：跳过，输出 INFO"使用已有配置文件"

- [ ] Task 2: Preflight 检查项适配 (AC: #6)
  - [ ] 2.1 `src/ato/preflight.py` 中 ato_yaml 检查项：将 HALT 改为 INFO，message 改为"ato.yaml 不存在，init 时将自动从 example 生成"
  - [ ] 2.2 确保 preflight 不因缺少 ato.yaml 而阻止 init 流程

- [ ] Task 3: 项目路径支持 (AC: #4, #5)
  - [ ] 3.1 确认现有 `init_command` 的 `project_path` 参数已支持路径指定（当前默认 "."）
  - [ ] 3.2 确保 ato.yaml 查找和生成使用 `project_path` 而非硬编码当前目录
  - [ ] 3.3 `ato start` 等后续命令的配置查找路径与 init 一致

- [ ] Task 4: 更新测试 (AC: #1-#6)
  - [ ] 4.1 新增测试：无 ato.yaml 时 init 自动生成
  - [ ] 4.2 新增测试：已有 ato.yaml 时 init 不覆盖
  - [ ] 4.3 新增测试：无 example 时报错
  - [ ] 4.4 新增测试：指定项目路径时正确工作

## Dev Notes

- 现有 `init_command` 已有 `project_path: Path` 参数（默认 "."），Task 3 主要是确认和完善
- `preflight.py` 中 `ato_yaml` 检查项对应的 hint 是 `"从 ato.yaml.example 复制并补全配置"`（cli.py:117），需同步更新
- 配置生成应在 preflight 检查之后、DB 初始化之前执行——因为 DB 初始化可能需要读取配置

### Project Structure Notes

- 改动集中在 cli.py 的 init 流程和 preflight.py 的检查逻辑

### References

- [Source: src/ato/cli.py:181-230] init_command 实现
- [Source: src/ato/cli.py:117] ato_yaml hint 文本
- [Source: src/ato/preflight.py] preflight 检查引擎
