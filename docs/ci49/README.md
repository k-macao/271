# 49 源标签 · 完整 CI 工作流文件（供手工复制）

本目录放的是**已改成 49 源口径、且内容完整**的工作流文件，供没有 `workflows` 权限的账号直接复制覆盖
（Agent 的 GitHub App 无 `workflows` 权限，无法推送 `.github/workflows/*` 的任何改动）。

| 文件 | 目标路径 | 说明 |
|---|---|---|
| `m.yml` | `.github/workflows/m.yml` | 49 源口径 + **完整 277 行**（deploy / wechat / daily 三个 job 齐全） |
| `github_workflow_new.yml` | `.github_workflow_new.yml`（仓库根的快照文件） | 49 源口径 + 完整 356 行 |

> ⚠️ **2026-10-07 事故记录（务必先看）**
> main 上的 `.github/workflows/m.yml` 被 Web 编辑器截断成 230 行，末行停在
> `- name: 🗣️ 动态抓取社区 (community_data.py · 49 `（值没写完），daily 任务从这一步往后的
> 4 个步骤 + 09:00 定时推送步骤全部丢失。该文件导致 workflow 校验失败：
> 10 月 7 日 08:56 / 08:57 两次 push 触发的运行都是 `failure` 且**零 job、无日志**（工作流根本没起来）。
> 修复方式：用本目录的 `m.yml` **整份覆盖**（见下），或应用仓库根的
> `docs/community49-ci-labels.patch`（已针对当前 main 重新生成并验证）。

## 修复 / 更新步骤（二选一）

```bash
# 方式 A：整份覆盖（推荐，最稳）
cp docs/ci49/m.yml .github/workflows/m.yml
cp docs/ci49/github_workflow_new.yml .github_workflow_new.yml
git diff --stat -- .github/workflows/m.yml .github_workflow_new.yml   # 应显示 2 个文件
git commit -m "ci: 修复 m.yml 截断并统一 49 源标签" && git push

# 方式 B：应用补丁（等价，已 `git apply --check` 验证）
git apply docs/community49-ci-labels.patch
git diff --stat -- .github/workflows/m.yml .github_workflow_new.yml
git commit -m "ci: 修复 m.yml 截断并统一 49 源标签" && git push
```

两种方式的结果**逐字相同**（用 `diff` 校验过）：`.github/workflows/m.yml` 277 行、
`.github_workflow_new.yml` 356 行，标签全部为 49。

修好后建议手动触发一次（Actions → `📄 Deploy Report to Pages + 📲 WeChat Push` → Run workflow，
勾选 `wechat_push`），确认三件事：`🗣️ 动态抓取社区 (… · 49 源)` 这一步跑到、页面部署成功、
`📲 推送完整报告到微信 (PushPlus · 定时宽松校验)` 打印 49 条「最新读取」标记。

## 这次相对上一版改了什么（仅 6 处「多少个源」文案）

| 位置 | 改动前 | 改动后 |
|---|---|---|
| `m.yml` 第 11 行（顶部管线注释） | `community_data.py → community_data.json (34 大社区 HTTP GET + 动态模板回退，每次刷新当天日期)` | 同左，`34` → `49` |
| `m.yml` 第 68 / 159 / 230 行（deploy / wechat / daily 三步） | `🗣️ 动态抓取社区 (community_data.py · 34 源)` | 同左，`34 源` → `49 源` |
| `github_workflow_new.yml` 第 13 行（顶部管线注释） | 同上的 `34 大社区` | `49 大社区` |
| `github_workflow_new.yml` 第 78 行（deploy 步骤） | `🗣️ 动态抓取社区 (community_data.py → community_data.json · 34 源)` | `· 49 源` |

改动不涉及任何 action、环境变量、命令与触发条件。即使还没修，CI 实际抓取的数量也已经是 49
（`community_data.py` 的目录 + `tools/wechat_push.py` 的
`EXPECTED_CHANNEL_COUNT = len(community_data.COMMUNITIES)`），**唯一后果是 workflow 起不来**。
