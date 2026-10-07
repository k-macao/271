# 49 源标签 · 完整 CI 工作流文件（供手工复制）

本目录放的是**已改成 49 源口径**的完整工作流文件，方便没有 `workflows` 权限的账号直接
复制覆盖（Agent 的 GitHub App 无 `workflows` 权限，无法推送 `.github/workflows/*` 的任何改动）。

| 文件 | 目标路径 | 改动 |
|---|---|---|
| `m.yml` | `.github/workflows/m.yml` | ① 顶部管线注释 `34 大社区` → `49 大社区`；② deploy / wechat / daily 三个 job 的步骤名 `· 34 源` → `· 49 源` |
| `github_workflow_new.yml` | `.github_workflow_new.yml`（仓库根的快照文件） | ① 顶部管线注释 `34 大社区` → `49 大社区`；② deploy job 步骤名 `· 34 源` → `· 49 源` |

除了这 6 处「多少个源」的文案，**其余内容与仓库当前版本逐字一致**（没有改任何 action、
环境变量、命令与触发条件）。等价的补丁版本见 [`../community49-ci-labels.patch`](../community49-ci-labels.patch)：

```bash
git apply docs/community49-ci-labels.patch     # 打补丁（等价于上面两份完整文件的替换）
# 或者直接复制：
cp docs/ci49/m.yml .github/workflows/m.yml
cp docs/ci49/github_workflow_new.yml .github_workflow_new.yml
git diff -- .github/workflows/m.yml .github_workflow_new.yml   # 应只有 6 行改动
```

## 为什么改这 6 处

| 位置 | 改动前 | 改动后 |
|---|---|---|
| `m.yml` 第 11 行 / `github_workflow_new.yml` 第 13 行（顶部管线注释） | `community_data.py → community_data.json (34 大社区 HTTP GET + 动态模板回退，每次刷新当天日期)` | 同左，`34` → `49` |
| `m.yml` 第 68 / 159 / 230 行（deploy / wechat / daily 三步） | `🗣️ 动态抓取社区 (community_data.py · 34 源)` | 同左，`34 源` → `49 源` |
| `github_workflow_new.yml` 第 78 行（deploy 步骤） | `🗣️ 动态抓取社区 (community_data.py → community_data.json · 34 源)` | 同左，`34 源` → `49 源` |

改完不影响任何逻辑：CI 里 `python3 community_data.py` 抓的就是 `community_data.py` 里
的 49 源目录，`tools/wechat_push.py` 的 `EXPECTED_CHANNEL_COUNT = len(community_data.COMMUNITIES)`
也已经是 49，不会因为步骤名文案而校验失败。
