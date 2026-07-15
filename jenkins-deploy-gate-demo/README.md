# Jenkins Deploy Gate Demo

这个目录是“一层 Jenkins 发布准入 Gate”的 demo 框架。它不修改业务代码，也不替代现有部署脚本；目标是演示 Jenkins 如何在真正上传、重启服务之前完成：

- 获取当前部署上下文
- 计算部署差量
- 调用 `ocr review`
- 生成准入报告和提测确认单草稿
- 根据规则决定是否继续部署

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `Jenkinsfile.demo` | Declarative Pipeline 示例，演示 Docker agent 中运行 gate |
| `gate-config.example.json` | 准入规则样例，包括阻断等级、风险文件模式、确认单必填项 |
| `scripts/run-deploy-gate.sh` | Jenkins 中调用的主入口，收集上下文、运行 OCR、调用判定脚本 |
| `scripts/evaluate_gate.py` | 解析 OCR JSON、风险文件、上下文，输出 gate 判定和确认单草稿 |

## 推荐流水线位置

Gate 应放在部署动作之前：

```text
Checkout
  -> Jenkins Deploy Gate
  -> Build/Test
  -> Package
  -> Upload/Restart
```

如果要把构建/测试结果也写入确认单，可以拆成：

```text
Checkout
  -> Jenkins Deploy Gate: review-only
  -> Build/Test
  -> Jenkins Deploy Gate: final-confirm
  -> Upload/Restart
```

当前 demo 先实现第一种：部署前 review + 生成确认单草稿。

## Jenkins 前置条件

### 方式 A：Docker agent

Jenkins 需要安装 `Docker Pipeline` 插件，并且实际执行节点能运行 Docker。

`r2-code-review:latest` 镜像里至少需要有：

```text
git
bash
python3
ocr
```

### 方式 B：普通 agent + docker run

如果 Jenkins 不支持 `agent { docker { ... } }`，也可以在普通节点里手动运行镜像：

```bash
docker run --rm \
  -v "$WORKSPACE:/workspace" \
  -w /workspace \
  -e TARGET_ENV \
  -e LAST_SUCCESS_DEPLOY_COMMIT \
  -e OCR_LLM_URL \
  -e OCR_LLM_TOKEN \
  -e OCR_LLM_MODEL \
  -e GITLAB_TOKEN \
  r2-code-review:latest \
  bash ci/jenkins-deploy-gate-demo/scripts/run-deploy-gate.sh
```

## 必要环境变量

| 变量 | 说明 |
| --- | --- |
| `TARGET_ENV` | 部署环境，例如 `dev`、`test`、`uat` |
| `LAST_SUCCESS_DEPLOY_COMMIT` | 当前环境上一次成功部署的 commit；用于计算部署差量 |
| `OCR_LLM_URL` | LLM API 地址 |
| `OCR_LLM_TOKEN` | LLM token |
| `OCR_LLM_MODEL` | LLM 模型名 |

## 可选环境变量

| 变量 | 说明 |
| --- | --- |
| `DEPLOY_COMMIT` | 默认取 `git rev-parse HEAD` |
| `DEPLOY_BRANCH` | 默认取当前分支名 |
| `GITLAB_PROJECT_ID` | GitLab 项目 ID |
| `GITLAB_PROJECT_URL` | GitLab 项目 URL |
| `GITLAB_MR_IID` | 关联 MR IID |
| `GITLAB_TOKEN` | 后续接 GitLab API 时使用；当前 demo 只透传上下文 |
| `GATE_CONFIG` | 默认 `ci/jenkins-deploy-gate-demo/gate-config.example.json` |
| `GATE_OUTPUT_DIR` | 默认 `gate-output` |

## 输出产物

运行后会生成：

| 文件 | 说明 |
| --- | --- |
| `gate-output/deploy-context.json` | 当前 Jenkins 部署上下文 |
| `gate-output/changed-files.txt` | `base_commit..deploy_commit` 的变更文件 |
| `gate-output/ocr-result.json` | OCR 原始 JSON 输出 |
| `gate-output/ocr-stderr.log` | OCR stderr |
| `gate-output/gate-report.json` | 准入判定结构化报告 |
| `gate-output/ai-agent-confirmation.md` | 提测确认单草稿，失败时也会生成 |
| `gate-output/AI-agent-confirmation.docx` | 基于 Word 模板追加 gate 摘要的提测确认单 |

## 判定逻辑

默认阻断规则：

- `ocr` 执行失败
- `base_commit` 为空
- OCR 发现 `critical` 或 `high`
- `medium` 数量超过 `gate-config.example.json` 里的阈值
- 确认单必填项缺失

默认不阻断但会写入 warning：

- 缺少 GitLab MR 信息
- 命中风险文件模式，例如配置、接口、权限、数据库相关文件

## 本地试跑

先确认 `ocr`、`python3` 可用，然后在仓库根目录执行：

```bash
export TARGET_ENV=test
export LAST_SUCCESS_DEPLOY_COMMIT=$(git rev-parse HEAD~1)
export OCR_LLM_URL=https://your-llm-gateway/v1
export OCR_LLM_TOKEN=your-token
export OCR_LLM_MODEL=your-model

bash ci/jenkins-deploy-gate-demo/scripts/run-deploy-gate.sh
```

如果只是验证判定脚本，不想真实调用 OCR，可以手工准备 `gate-output/ocr-result.json` 后直接运行 `evaluate_gate.py`。

## 后续接真实系统时要补的点

1. **GitLab API 查询**
   根据 `DEPLOY_COMMIT` 查询关联 MR、需求编号、作者、标题、描述，并写入 `deploy-context.json`。

2. **上次成功部署 commit**
   demo 从 `LAST_SUCCESS_DEPLOY_COMMIT` 读取。生产建议从 Jenkins 成功记录、部署平台、制品元数据或环境版本文件读取。

3. **确认单 docx**
   当前会基于 `templates/AI-agent-confirmation.docx` 追加 gate 摘要。后续可以把 Markdown 结构进一步映射到模板表格单元格。

4. **测试结果**
   当前 demo 未读取 JUnit/Coverage 报告。接入后可把 `mvn test`、覆盖率、测试报告链接写入确认单第 5 部分。

5. **部署脚本衔接**
   Gate 通过后再进入上传和 restart 阶段。不要把 gate 放在远端启动之后。
